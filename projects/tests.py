import hashlib
import hmac
import json
import secrets
from datetime import date
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from projects.github_utils import commit_was_pushed_from_app, verify_github_signature
from projects.models import (
    AuditLogAction, Project, ProjectDomain, ProjectRepoStats, ProjectRequirementSection, ProjectRole,
    ProjectSkillRequirement, ProjectTask, ProjectTaskParticipation, ResourceAccess, TaskResourceAccess,
    UserProjectRole,
)
from users.models import User, UserRequest


def make_user(name):
    suffix = secrets.token_hex(4)
    return User.objects.create_user(
        username=f'{name}_{suffix}',
        email=f'{name}_{suffix}@example.com',
        password='CorrectHorseBatteryStaple123',
        birthday=date(2000, 1, 1),
    )


class ProjectCreationTests(TestCase):
    def setUp(self):
        # ratelimit counters live outside the DB transaction rollback (Redis),
        # so clear them for a clean slate on every test - same reasoning as
        # users.tests.UsersTests.setUp.
        cache.clear()
        suffix = secrets.token_hex(4)
        # NOTE: prefix picked deliberately - 'projectcreator_' shares enough
        # characters with 'CorrectHorseBatteryStaple123' that difflib's
        # quick_ratio() (used by UserAttributeSimilarityValidator) flags them
        # as "too similar" on ~3% of random suffixes, making create_user()
        # intermittently return None. 'projowner_' doesn't collide.
        self.user = User.objects.create_user(
            username=f'projowner_{suffix}',
            email=f'projowner_{suffix}@example.com',
            password='CorrectHorseBatteryStaple123',
            birthday=date(2000, 1, 1),
        )

    @staticmethod
    def _create_url():
        return reverse('users:create_project')

    def _post(self, payload):
        return self.client.post(self._create_url(), data=json.dumps(payload), content_type='application/json')

    # ---- happy path ----

    def test_create_project_minimal_succeeds(self):
        self.client.force_login(self.user)
        response = self._post({'name': f'proj_{secrets.token_hex(4)}', 'description': 'a project'})
        self.assertEqual(response.status_code, 201)
        data = response.json()['project']
        self.assertEqual(data['needed_skills'], {})
        self.assertEqual(data['github_repos'], [])
        self.assertTrue(Project.objects.filter(id=data['id']).exists())

    def test_create_project_makes_creator_the_owner_with_a_role(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({'name': name, 'description': 'a project'})
        project = Project.objects.get(id=response.json()['project']['id'])
        self.assertEqual(project.owner_id, self.user.id)
        role = UserProjectRole.objects.get_user_role_in_project(project, self.user)
        self.assertNotEqual(role, 'visitor', "the creator must automatically get a real role, not fall back to visitor")

    # ---- needed_skills: dict of {domain: [skill, ...]} ----

    def test_create_project_with_needed_skills_creates_sections_and_requirements(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({
            'name': name,
            'description': 'a project',
            'needed_skills': {'Backend': ['Python', 'SpringBoot'], 'Frontend': ['React']},
        })
        self.assertEqual(response.status_code, 201)
        project = Project.objects.get(id=response.json()['project']['id'])

        sections = {s.name for s in ProjectRequirementSection.objects.filter(project=project)}
        self.assertEqual(sections, {'Backend', 'Frontend'})

        backend_skills = set(ProjectSkillRequirement.objects.filter(
            section__project=project, section__name='Backend'
        ).values_list('name', flat=True))
        self.assertEqual(backend_skills, {'Python', 'SpringBoot'})

        response_skills = response.json()['project']['needed_skills']
        self.assertEqual(set(response_skills.keys()), {'Backend', 'Frontend'})
        self.assertEqual({s['skill'] for s in response_skills['Backend']}, {'Python', 'SpringBoot'})

    def test_create_project_rejects_needed_skills_that_are_not_a_domain_to_skill_list_mapping(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({
            'name': name,
            'description': 'a project',
            'needed_skills': {'Backend': 'Python'},  # should be a list, not a bare string
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Project.objects.filter(name=name).exists())

    def test_create_project_rejects_needed_skills_given_as_a_list_instead_of_a_dict(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({
            'name': name,
            'description': 'a project',
            'needed_skills': ['Backend', 'Frontend'],
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Project.objects.filter(name=name).exists())

    # ---- github_repos: list of {github_repo_name, github_repo_link, github_repo_access_token} ----

    def test_create_project_with_github_repos_creates_and_links_repo_stats(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({
            'name': name,
            'description': 'a project',
            'github_repos': [{
                'github_repo_name': 'my-repo',
                'github_repo_link': 'https://github.com/someowner/my-repo',
                'github_repo_access_token': 'ghp_supersecrettoken',
            }],
        })
        self.assertEqual(response.status_code, 201)
        project = Project.objects.get(id=response.json()['project']['id'])

        repo_stat = project.repo_stats.get()
        self.assertEqual(repo_stat.github_repo_name, 'my-repo')
        self.assertEqual(repo_stat.github_repo_link, 'https://github.com/someowner/my-repo')
        self.assertEqual(
            repo_stat.github_token, 'ghp_supersecrettoken',
            "github_repo_access_token from the request must land in the model's github_token field"
        )

    def test_create_project_response_never_echoes_back_the_access_token(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({
            'name': name,
            'description': 'a project',
            'github_repos': [{
                'github_repo_name': 'my-repo',
                'github_repo_link': 'https://github.com/someowner/my-repo',
                'github_repo_access_token': 'ghp_supersecrettoken',
            }],
        })
        self.assertNotIn('ghp_supersecrettoken', response.content.decode())
        repos = response.json()['project']['github_repos']
        self.assertEqual(len(repos), 1)
        self.assertNotIn('github_repo_access_token', repos[0])
        self.assertNotIn('github_token', repos[0])

    def test_create_project_github_repo_access_token_is_optional(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({
            'name': name,
            'description': 'a project',
            'github_repos': [{
                'github_repo_name': 'public-repo',
                'github_repo_link': 'https://github.com/someowner/public-repo',
            }],
        })
        self.assertEqual(response.status_code, 201)
        project = Project.objects.get(id=response.json()['project']['id'])
        self.assertEqual(project.repo_stats.get().github_token, '')

    def test_create_project_rejects_github_repos_missing_required_keys(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({
            'name': name,
            'description': 'a project',
            'github_repos': [{'github_repo_name': 'no-link-repo'}],  # github_repo_link missing
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Project.objects.filter(name=name).exists())
        self.assertFalse(ProjectRepoStats.objects.filter(github_repo_name='no-link-repo').exists())

    def test_create_project_rejects_github_repos_given_as_a_dict_instead_of_a_list(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        response = self._post({
            'name': name,
            'description': 'a project',
            'github_repos': {
                'github_repo_name': 'my-repo',
                'github_repo_link': 'https://github.com/someowner/my-repo',
            },
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Project.objects.filter(name=name).exists())

    # ---- pre-existing validation, now on the JSON path ----

    def test_create_project_rejects_duplicate_name(self):
        self.client.force_login(self.user)
        name = f'proj_{secrets.token_hex(4)}'
        first = self._post({'name': name, 'description': 'first'})
        self.assertEqual(first.status_code, 201)

        second = self._post({'name': name, 'description': 'second'})
        self.assertEqual(second.status_code, 400)
        self.assertEqual(Project.objects.filter(name=name).count(), 1)

    def test_create_project_rejects_name_that_fails_slug_validation(self):
        self.client.force_login(self.user)
        response = self._post({'name': 'not a valid slug!!!', 'description': 'a project'})
        self.assertEqual(response.status_code, 400)

    def test_create_project_missing_name_returns_400_not_500(self):
        """
        Before the JSON migration this read request.POST['name'] directly -
        a missing field raised an unhandled KeyError (500). It must now be a
        clean 400.
        """
        self.client.force_login(self.user)
        response = self._post({'description': 'a project'})
        self.assertEqual(response.status_code, 400)

    def test_create_project_invalid_json_body_returns_400_not_500(self):
        self.client.force_login(self.user)
        response = self.client.post(self._create_url(), data='not json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_create_project_requires_authentication(self):
        response = self._post({'name': f'proj_{secrets.token_hex(4)}', 'description': 'a project'})
        self.assertEqual(response.status_code, 302)

    def test_create_project_get_returns_ready_status(self):
        self.client.force_login(self.user)
        response = self.client.get(self._create_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ready')

    # ---- rate limiting ----

    def test_create_project_rate_limit_blocks_after_20_requests_per_user(self):
        """create_project rate-limits POST by 'user' at 20/m."""
        self.client.force_login(self.user)

        for attempt in range(20):
            response = self._post({'name': f'ratelimitproj_{attempt}_{secrets.token_hex(4)}', 'description': 'd'})
            self.assertEqual(
                response.status_code, 201,
                f"attempt {attempt + 1} should succeed normally, got {response.status_code}"
            )

        blocked_response = self._post({
            'name': f'ratelimitproj_blocked_{secrets.token_hex(4)}', 'description': 'd'
        })
        self.assertEqual(
            blocked_response.status_code, 403,
            "21st POST within a minute from the same user should be rate-limited (user, 20/m)"
        )


class ProjectMembershipMixin:
    """Shared fixture: a project with an owner, a non-owner member ('developer' role), and a total outsider."""

    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.member = make_user('projmember')
        self.outsider = make_user('projvisitor')

        self.project = Project.objects.create_project(self.owner.id, f'crudproj_{secrets.token_hex(4)}', 'a project')
        developer_role = ProjectRole.objects.get(name='developer')
        UserProjectRole.objects.give_role_to_user(self.project.id, self.member.id, developer_role)


class ProjectDomainsCrudTests(ProjectMembershipMixin, TestCase):
    @staticmethod
    def _url(project_id):
        return reverse('projects:project-domains', kwargs={'id': project_id})

    def _post(self, project_id, payload):
        return self.client.post(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    def _delete(self, project_id, payload):
        return self.client.delete(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    # ---- business logic ----

    def test_get_lists_domains(self):
        ProjectDomain.objects.add_domains_to_project(self.project, ['Backend', 'Frontend'])
        self.client.force_login(self.owner)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 200)
        names = {d['domain'] for d in response.json()['domains']}
        self.assertEqual(names, {'Backend', 'Frontend'})

    def test_get_nonexistent_project_returns_404(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._url(self.project.id + 100_000))
        self.assertEqual(response.status_code, 404)

    def test_owner_can_add_domains(self):
        self.client.force_login(self.owner)
        response = self._post(self.project.id, {'newDomains': ['Backend', 'Frontend']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(ProjectDomain.objects.filter(project=self.project).values_list('domain', flat=True)),
            {'Backend', 'Frontend'}
        )

    def test_owner_can_remove_domains(self):
        ProjectDomain.objects.add_domains_to_project(self.project, ['Backend', 'Frontend'])
        self.client.force_login(self.owner)
        response = self._delete(self.project.id, {'removedDomains': ['Backend']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(ProjectDomain.objects.filter(project=self.project).values_list('domain', flat=True)),
            {'Frontend'}
        )

    def test_remove_with_no_domains_given_returns_400(self):
        self.client.force_login(self.owner)
        response = self._delete(self.project.id, {'removedDomains': []})
        self.assertEqual(response.status_code, 400)

    # ---- security ----

    def test_get_requires_authentication(self):
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 302)

    def test_outsider_cannot_view_domains(self):
        """
        Regression test: _get_project_domains used to have no role check at
        all - any authenticated user (even a total outsider) could list any
        project's domains just by knowing its id. Now consistent with every
        other GET in this file.
        """
        ProjectDomain.objects.add_domains_to_project(self.project, ['Backend'])
        self.client.force_login(self.outsider)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 403)

    def test_non_owner_member_can_view_domains(self):
        """Viewing only requires membership, not can_change_project_settings - unlike POST/DELETE."""
        ProjectDomain.objects.add_domains_to_project(self.project, ['Backend'])
        self.client.force_login(self.member)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 200)

    def test_non_owner_member_cannot_add_domains(self):
        """
        Regression test for a real bug: _add_project_domains's unauthorized
        branch used to build a JsonResponse with no `status=` kwarg (just a
        'code': 403 field buried in the body), so the real HTTP status was
        always 200 - a frontend checking the status code the normal way
        would have seen success on a rejected request.
        """
        self.client.force_login(self.member)
        response = self._post(self.project.id, {'newDomains': ['Backend']})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['status'], 'Unauthorized access')
        self.assertFalse(ProjectDomain.objects.filter(project=self.project).exists())

    def test_outsider_cannot_add_domains(self):
        self.client.force_login(self.outsider)
        response = self._post(self.project.id, {'newDomains': ['Backend']})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['status'], 'Unauthorized access')
        self.assertFalse(ProjectDomain.objects.filter(project=self.project).exists())

    def test_non_owner_member_cannot_remove_domains(self):
        ProjectDomain.objects.add_domains_to_project(self.project, ['Backend'])
        self.client.force_login(self.member)
        response = self._delete(self.project.id, {'removedDomains': ['Backend']})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProjectDomain.objects.filter(project=self.project, domain='Backend').exists())

    # ---- rate limiting ----

    def test_get_rate_limit_blocks_after_120_requests_per_user(self):
        """api_project_domains rate-limits GET by 'user' at 120/m."""
        self.client.force_login(self.owner)
        for attempt in range(120):
            response = self.client.get(self._url(self.project.id))
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self.client.get(self._url(self.project.id))
        self.assertEqual(blocked.status_code, 403, "121st GET within a minute should be rate-limited (user, 120/m)")

    def test_post_rate_limit_blocks_after_30_requests_per_user(self):
        """api_project_domains rate-limits POST by 'user' at 30/m."""
        self.client.force_login(self.owner)
        for attempt in range(30):
            response = self._post(self.project.id, {'newDomains': [f'Domain{attempt}']})
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._post(self.project.id, {'newDomains': ['DomainBlocked']})
        self.assertEqual(blocked.status_code, 403, "31st POST within a minute should be rate-limited (user, 30/m)")


class ProjectRequirementsCrudTests(ProjectMembershipMixin, TestCase):
    @staticmethod
    def _sections_url(project_id):
        return reverse('projects:project-requirement-sections', kwargs={'id': project_id})

    @staticmethod
    def _requirements_url(project_id):
        return reverse('projects:project-requirements', kwargs={'id': project_id})

    def _post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type='application/json')

    def _delete(self, url, payload):
        return self.client.delete(url, data=json.dumps(payload), content_type='application/json')

    # ---- sections: business logic ----

    def test_owner_can_add_sections(self):
        self.client.force_login(self.owner)
        response = self._post(self._sections_url(self.project.id), {'newSections': ['Backend', 'Frontend']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(ProjectRequirementSection.objects.filter(project=self.project).values_list('name', flat=True)),
            {'Backend', 'Frontend'}
        )

    def test_owner_can_remove_sections(self):
        ProjectRequirementSection.objects.add_requirement_sections(self.project, ['Backend'])
        self.client.force_login(self.owner)
        response = self._delete(self._sections_url(self.project.id), {'removedSections': ['Backend']})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectRequirementSection.objects.filter(project=self.project, name='Backend').exists())

    def test_add_sections_with_empty_list_returns_400(self):
        self.client.force_login(self.owner)
        response = self._post(self._sections_url(self.project.id), {'newSections': []})
        self.assertEqual(response.status_code, 400)

    # ---- requirements: business logic ----

    def test_get_requirements_grouped_by_section(self):
        section = ProjectRequirementSection.objects.add_requirement_sections(self.project, ['Backend'])[0]
        ProjectSkillRequirement.objects.add_skill_requirements(section, ['Python'])
        self.client.force_login(self.owner)
        response = self.client.get(self._requirements_url(self.project.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s['skill'] for s in response.json()['requirements']['Backend']], ['Python'])

    def test_owner_can_add_requirements_to_an_existing_section(self):
        ProjectRequirementSection.objects.add_requirement_sections(self.project, ['Backend'])
        self.client.force_login(self.owner)
        response = self._post(self._requirements_url(self.project.id), {'newRequirements': [['Backend', 'Python']]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ProjectSkillRequirement.objects.filter(section__project=self.project, name='Python').exists())

    def test_owner_can_remove_requirements(self):
        section = ProjectRequirementSection.objects.add_requirement_sections(self.project, ['Backend'])[0]
        ProjectSkillRequirement.objects.add_skill_requirements(section, ['Python'])
        self.client.force_login(self.owner)
        response = self._delete(self._requirements_url(self.project.id), {'removedRequirements': [['Backend', 'Python']]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectSkillRequirement.objects.filter(section__project=self.project, name='Python').exists())

    def test_BUG_adding_requirement_to_a_nonexistent_section_returns_500(self):
        """
        *** BUG, not a requirement ***
        _add_project_requirements does section_manager.get(project=project,
        name=key) - a section name that doesn't exist yet raises
        ProjectRequirementSection.DoesNotExist, uncaught by any specific
        except clause, so it falls to the generic except -> 500. Should be a
        clean 400/404 ("section does not exist - create it first").
        """
        self.client.force_login(self.owner)
        response = self._post(self._requirements_url(self.project.id), {'newRequirements': [['NoSuchSection', 'Python']]})
        self.assertEqual(
            response.status_code, 500,
            "if this starts failing, someone added section-existence validation - update this test"
        )

    # ---- security ----

    def test_sections_get_requires_authentication_via_requirements_endpoint(self):
        response = self.client.get(self._requirements_url(self.project.id))
        self.assertEqual(response.status_code, 302)

    def test_non_owner_member_cannot_add_sections(self):
        self.client.force_login(self.member)
        response = self._post(self._sections_url(self.project.id), {'newSections': ['Backend']})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectRequirementSection.objects.filter(project=self.project).exists())

    def test_non_owner_member_cannot_add_requirements(self):
        ProjectRequirementSection.objects.add_requirement_sections(self.project, ['Backend'])
        self.client.force_login(self.member)
        response = self._post(self._requirements_url(self.project.id), {'newRequirements': [['Backend', 'Python']]})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectSkillRequirement.objects.filter(section__project=self.project).exists())

    def test_outsider_cannot_view_requirements(self):
        """Regression test: _get_project_requirements used to have no role check at all."""
        self.client.force_login(self.outsider)
        response = self.client.get(self._requirements_url(self.project.id))
        self.assertEqual(response.status_code, 403)

    def test_non_owner_member_can_view_requirements(self):
        self.client.force_login(self.member)
        response = self.client.get(self._requirements_url(self.project.id))
        self.assertEqual(response.status_code, 200)

    # ---- rate limiting ----

    def test_requirements_post_rate_limit_blocks_after_30_requests_per_user(self):
        """api_project_requirements rate-limits POST by 'user' at 30/m."""
        ProjectRequirementSection.objects.add_requirement_sections(self.project, ['Backend'])
        self.client.force_login(self.owner)
        for attempt in range(30):
            response = self._post(self._requirements_url(self.project.id), {'newRequirements': [['Backend', f'Skill{attempt}']]})
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._post(self._requirements_url(self.project.id), {'newRequirements': [['Backend', 'SkillBlocked']]})
        self.assertEqual(blocked.status_code, 403, "31st POST within a minute should be rate-limited (user, 30/m)")

    def test_sections_post_rate_limit_blocks_after_30_requests_per_user(self):
        """api_project_requirement_sections rate-limits POST by 'user' at 30/m."""
        self.client.force_login(self.owner)
        for attempt in range(30):
            response = self._post(self._sections_url(self.project.id), {'newSections': [f'Section{attempt}']})
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._post(self._sections_url(self.project.id), {'newSections': ['SectionBlocked']})
        self.assertEqual(blocked.status_code, 403, "31st POST within a minute should be rate-limited (user, 30/m)")


class ProjectTasksCrudTests(ProjectMembershipMixin, TestCase):
    @staticmethod
    def _url(project_id):
        return reverse('projects:project-tasks', kwargs={'id': project_id})

    def _post(self, project_id, payload):
        return self.client.post(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    def _delete(self, project_id, payload):
        return self.client.delete(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    @staticmethod
    def _task_payload(title='Task', description='desc', start='2025-01-01', end='2025-02-01'):
        return {'title': title, 'description': description, 'start_date': start, 'end_date': end}

    # ---- business logic ----

    def test_owner_can_add_a_task(self):
        self.client.force_login(self.owner)
        response = self._post(self.project.id, self._task_payload())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ProjectTask.objects.filter(id=response.json()['task_id'], project=self.project).exists())

    def test_owner_can_remove_a_task(self):
        self.client.force_login(self.owner)
        task_id = self._post(self.project.id, self._task_payload(title='ToRemove')).json()['task_id']
        response = self._delete(self.project.id, {'removedTasks': ['ToRemove']})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProjectTask.objects.filter(id=task_id).exists())

    def test_duplicate_task_name_is_rejected(self):
        self.client.force_login(self.owner)
        self._post(self.project.id, self._task_payload(title='Dup'))
        second = self._post(self.project.id, self._task_payload(title='Dup'))
        self.assertEqual(second.status_code, 500)  # add_task_to_project returns [] on duplicate name -> "could not be created"
        self.assertEqual(ProjectTask.objects.filter(project=self.project, name='Dup').count(), 1)

    def test_start_date_after_end_date_is_rejected(self):
        self.client.force_login(self.owner)
        response = self._post(self.project.id, self._task_payload(start='2025-06-01', end='2025-01-01'))
        self.assertEqual(response.status_code, 500)
        self.assertFalse(ProjectTask.objects.filter(project=self.project).exists())

    def test_owner_only_view_returns_404_when_no_tasks_exist(self):
        """
        Documents current behavior: _get_project_tasks returns 404 (not 200
        with an empty list) when a project genuinely has zero tasks - so an
        empty task list and "you can't see this" both surface as errors to
        the caller, just with different status codes (404 vs 403).
        """
        self.client.force_login(self.owner)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 404)

    # ---- security ----

    def test_get_requires_authentication(self):
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 302)

    def test_outsider_cannot_add_a_task(self):
        """
        Regression test for a real bug: unlike every sibling endpoint in
        this file, _add_project_task used to never check the caller's role
        in the project at all - not even "not a visitor". A completely
        unaffiliated user could create a task in any project just by
        knowing its id. Now gated on can_add_tasks.
        """
        self.client.force_login(self.outsider)
        response = self._post(self.project.id, self._task_payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectTask.objects.filter(project=self.project).exists())

    def test_developer_can_add_a_task(self):
        """'developer' has can_add_tasks=True in DEFAULT_PROJECT_ROLES."""
        self.client.force_login(self.member)
        response = self._post(self.project.id, self._task_payload())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ProjectTask.objects.filter(id=response.json()['task_id'], project=self.project).exists())

    def test_developer_with_can_add_tasks_permission_still_cannot_view_the_task_list(self):
        """
        Documents an inconsistency, not confirming it's correct:
        DEFAULT_PROJECT_ROLES gives 'developer' can_add_tasks=True and
        can_modify_tasks=True, but _get_project_tasks (GET, listing tasks)
        and _remove_project_tasks (DELETE) both gate on
        can_change_project_settings, which only 'owner' has. So a developer
        who's supposedly allowed to work with tasks can't even list them.
        """
        self.client.force_login(self.owner)
        self._post(self.project.id, self._task_payload())

        self.client.force_login(self.member)  # 'developer' role
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 403)

    def test_non_owner_member_cannot_remove_tasks(self):
        self.client.force_login(self.owner)
        self._post(self.project.id, self._task_payload(title='Protected'))

        self.client.force_login(self.member)
        response = self._delete(self.project.id, {'removedTasks': ['Protected']})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProjectTask.objects.filter(project=self.project, name='Protected').exists())

    # ---- rate limiting ----

    def test_post_rate_limit_blocks_after_30_requests_per_user(self):
        """api_project_tasks rate-limits POST by 'user' at 30/m."""
        self.client.force_login(self.owner)
        for attempt in range(30):
            response = self._post(self.project.id, self._task_payload(title=f'RateTask{attempt}'))
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._post(self.project.id, self._task_payload(title='RateTaskBlocked'))
        self.assertEqual(blocked.status_code, 403, "31st POST within a minute should be rate-limited (user, 30/m)")


class ProjectRolesCrudTests(ProjectMembershipMixin, TestCase):
    @staticmethod
    def _url(project_id):
        return reverse('projects:project-roles', kwargs={'id': project_id})

    def _post(self, project_id, payload):
        return self.client.post(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    @staticmethod
    def _new_role_payload(name='QA', **overrides):
        payload = {
            'name': name, 'can_accept_invites': False, 'can_invite_others': False, 'can_kick_others': False,
            'can_change_roles': False, 'can_create_branches': False, 'can_merge_branches': False,
            'can_delete_branches': False, 'can_add_tasks': False, 'can_delete_tasks': False,
            'can_modify_tasks': False, 'can_change_project_settings': False,
        }
        payload.update(overrides)
        return payload

    # ---- business logic ----

    def test_owner_can_list_roles(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 200)
        role_names = {r['name'] for r in response.json()['roles']}
        self.assertIn('developer', role_names)

    def test_owner_can_create_a_new_role(self):
        self.client.force_login(self.owner)
        response = self._post(self.project.id, self._new_role_payload(name='QA'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ProjectRole.objects.filter(id=response.json()['role_id'], name='QA').exists())

    def test_cannot_recreate_the_owner_role(self):
        self.client.force_login(self.owner)
        response = self._post(self.project.id, self._new_role_payload(
            name='SuperOwner',
            can_accept_invites=True, can_invite_others=True, can_kick_others=True, can_change_roles=True,
            can_create_branches=True, can_merge_branches=True, can_delete_branches=True, can_add_tasks=True,
            can_delete_tasks=True, can_modify_tasks=True, can_change_project_settings=True,
        ))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectRole.objects.filter(name='SuperOwner').exists())

    # ---- security ----

    def test_get_requires_authentication(self):
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 302)

    def test_non_owner_member_cannot_list_roles(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 403)

    def test_non_owner_member_cannot_create_a_role(self):
        self.client.force_login(self.member)
        response = self._post(self.project.id, self._new_role_payload(name='QA'))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectRole.objects.filter(name='QA').exists())

    # ---- rate limiting ----

    def test_post_rate_limit_blocks_after_20_requests_per_user(self):
        """api_project_roles rate-limits POST by 'user' at 20/m."""
        self.client.force_login(self.owner)
        for attempt in range(20):
            response = self._post(self.project.id, self._new_role_payload(name=f'Role{attempt}'))
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._post(self.project.id, self._new_role_payload(name='RoleBlocked'))
        self.assertEqual(blocked.status_code, 403, "21st POST within a minute should be rate-limited (user, 20/m)")


class ProjectPushPolicyCrudTests(ProjectMembershipMixin, TestCase):
    @staticmethod
    def _url(project_id):
        return reverse('projects:project-push-policy', kwargs={'id': project_id})

    def _post(self, project_id, payload):
        return self.client.post(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    # ---- business logic ----

    def test_get_returns_current_policy(self):
        self.client.force_login(self.owner)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['can_only_modify_from_app'], False)
        self.assertEqual(response.json()['flagged_external_push'], False)

    def test_owner_can_enable_push_policy(self):
        """No linked GitHub repos on this test project, so no real GitHub calls happen along this path."""
        self.client.force_login(self.owner)
        response = self._post(self.project.id, {'can_only_modify_from_app': True})
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertTrue(self.project.can_only_modify_from_app)

    def test_post_missing_field_returns_400(self):
        self.client.force_login(self.owner)
        response = self._post(self.project.id, {})
        self.assertEqual(response.status_code, 400)

    def test_delete_clears_flagged_external_push(self):
        self.project.flagged_external_push = True
        self.project.save(update_fields=['flagged_external_push'])
        self.client.force_login(self.owner)
        response = self.client.delete(self._url(self.project.id))
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertFalse(self.project.flagged_external_push)

    # ---- security ----

    def test_get_requires_authentication(self):
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 302)

    def test_non_owner_member_cannot_change_push_policy(self):
        self.client.force_login(self.member)
        response = self._post(self.project.id, {'can_only_modify_from_app': True})
        self.assertEqual(response.status_code, 403)
        self.project.refresh_from_db()
        self.assertFalse(self.project.can_only_modify_from_app)

    def test_outsider_cannot_view_push_policy(self):
        """Regression test: _get_project_push_policy used to have no role check at all."""
        self.client.force_login(self.outsider)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 403)

    def test_non_owner_member_can_view_push_policy(self):
        self.client.force_login(self.member)
        response = self.client.get(self._url(self.project.id))
        self.assertEqual(response.status_code, 200)

    # ---- rate limiting ----

    def test_post_rate_limit_blocks_after_20_requests_per_user(self):
        """api_project_push_policy rate-limits POST by 'user' at 20/m."""
        self.client.force_login(self.owner)
        for attempt in range(20):
            response = self._post(self.project.id, {'can_only_modify_from_app': attempt % 2 == 0})
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._post(self.project.id, {'can_only_modify_from_app': True})
        self.assertEqual(blocked.status_code, 403, "21st POST within a minute should be rate-limited (user, 20/m)")


class ProjectJoinRequestTests(ProjectMembershipMixin, TestCase):
    @staticmethod
    def _join_url(project_id):
        return reverse('projects:send-project-join-request', kwargs={'project_id': project_id})

    @staticmethod
    def _handle_url():
        return reverse('projects:handle-project-join-request')

    def _join(self, project_id):
        return self.client.post(self._join_url(project_id))

    def _handle(self, payload):
        return self.client.post(self._handle_url(), data=json.dumps(payload), content_type='application/json')

    # ---- business logic: send ----

    def test_outsider_can_request_to_join(self):
        self.client.force_login(self.outsider)
        response = self._join(self.project.id)
        self.assertEqual(response.status_code, 200)
        req = UserRequest.objects.get(sender=self.outsider, request_type='project')
        self.assertEqual(req.receiver_id, self.owner.id)
        self.assertEqual(req.target, str(self.project.id))
        self.assertEqual(req.status, 'pending')

    def test_existing_member_cannot_request_to_join(self):
        self.client.force_login(self.member)
        response = self._join(self.project.id)
        self.assertEqual(response.status_code, 400)

    def test_duplicate_join_request_returns_400(self):
        self.client.force_login(self.outsider)
        self._join(self.project.id)
        second = self._join(self.project.id)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(UserRequest.objects.filter(sender=self.outsider, request_type='project').count(), 1)

    def test_join_nonexistent_project_returns_404(self):
        self.client.force_login(self.outsider)
        response = self._join(self.project.id + 100_000)
        self.assertEqual(response.status_code, 404)

    def test_join_requires_authentication(self):
        response = self._join(self.project.id)
        self.assertEqual(response.status_code, 302)

    def test_join_rate_limit_blocks_after_20_requests_per_user(self):
        """api_request_project_join rate-limits POST by 'user' at 20/m."""
        projects = [
            Project.objects.create_project(self.owner.id, f'joinrl_{i}_{secrets.token_hex(4)}', 'd')
            for i in range(21)
        ]
        self.client.force_login(self.outsider)
        for attempt, proj in enumerate(projects[:20]):
            response = self._join(proj.id)
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._join(projects[20].id)
        self.assertEqual(blocked.status_code, 403, "21st POST within a minute should be rate-limited (user, 20/m)")

    # ---- business logic: handle ----

    def test_handle_accept_adds_member_with_viewer_role(self):
        """
        Regression test for a real bug: api_handle_project_join_request used
        to reference ProjectRole.objects.get(name='newbie'), a role that was
        never defined anywhere - accepting a join request always 500'd.
        """
        self.client.force_login(self.outsider)
        self._join(self.project.id)

        self.client.force_login(self.owner)
        response = self._handle({'action': 'accept', 'sender_id': self.outsider.id, 'receiver_id': self.owner.id})
        self.assertEqual(response.status_code, 200)
        role = UserProjectRole.objects.get(project=self.project, user=self.outsider)
        self.assertEqual(role.role.name, 'viewer')
        self.assertEqual(UserRequest.objects.get(sender=self.outsider, request_type='project').status, 'accepted')

    def test_handle_reject_declines_without_adding_membership(self):
        """
        Regression test for a real bug: the 'reject'/'decline' branch used
        to be a copy-paste of the accept branch - it added the sender as a
        project member (role='newbie', which also didn't exist) and marked
        the request 'accepted', the opposite of what rejecting should do. A
        second, unreachable branch further down had the actually-correct
        "just decline" logic, but 'reject'/'decline' matched the broken
        branch first since elif chains take the first match.
        """
        self.client.force_login(self.outsider)
        self._join(self.project.id)

        self.client.force_login(self.owner)
        response = self._handle({'action': 'reject', 'sender_id': self.outsider.id, 'receiver_id': self.owner.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserRequest.objects.get(sender=self.outsider, request_type='project').status, 'declined')
        self.assertFalse(UserProjectRole.objects.filter(project=self.project, user=self.outsider).exists())

    def test_handle_deny_declines_without_adding_membership(self):
        self.client.force_login(self.outsider)
        self._join(self.project.id)

        self.client.force_login(self.owner)
        response = self._handle({'action': 'deny', 'sender_id': self.outsider.id, 'receiver_id': self.owner.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserRequest.objects.get(sender=self.outsider, request_type='project').status, 'declined')
        self.assertFalse(UserProjectRole.objects.filter(project=self.project, user=self.outsider).exists())

    def test_handle_missing_parameters_returns_400(self):
        self.client.force_login(self.owner)
        response = self._handle({'action': 'accept'})
        self.assertEqual(response.status_code, 400)

    def test_handle_request_not_found_returns_404(self):
        self.client.force_login(self.owner)
        response = self._handle({'action': 'accept', 'sender_id': self.outsider.id, 'receiver_id': self.owner.id})
        self.assertEqual(response.status_code, 404)

    def test_handle_unknown_action_returns_400(self):
        self.client.force_login(self.outsider)
        self._join(self.project.id)

        self.client.force_login(self.owner)
        response = self._handle({'action': 'do-a-barrel-roll', 'sender_id': self.outsider.id, 'receiver_id': self.owner.id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(UserRequest.objects.get(sender=self.outsider, request_type='project').status, 'pending')

    # ---- security ----

    def test_handle_requires_authentication(self):
        response = self._handle({'action': 'accept', 'sender_id': 1, 'receiver_id': 2})
        self.assertEqual(response.status_code, 302)

    def test_handle_by_non_receiver_is_rejected(self):
        """
        Regression test for a real bug: api_handle_project_join_request
        never checked that request.user was actually the request's
        receiver - any authenticated user who knew (or guessed, since
        they're sequential ids) a sender_id/receiver_id pair could accept
        or decline a join request addressed to somebody else.
        """
        self.client.force_login(self.outsider)
        self._join(self.project.id)

        third_party = make_user('projthirdparty')
        self.client.force_login(third_party)
        response = self._handle({'action': 'accept', 'sender_id': self.outsider.id, 'receiver_id': self.owner.id})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(UserRequest.objects.get(sender=self.outsider, request_type='project').status, 'pending')
        self.assertFalse(UserProjectRole.objects.filter(project=self.project, user=self.outsider).exists())

    def test_handle_rate_limit_blocks_after_20_requests_per_user(self):
        """api_handle_project_join_request rate-limits POST by 'user' at 20/m."""
        senders = [make_user(f'projjoinrl_{i}') for i in range(21)]
        for sender in senders:
            self.client.force_login(sender)
            self._join(self.project.id)

        self.client.force_login(self.owner)
        for attempt, sender in enumerate(senders[:20]):
            response = self._handle({'action': 'accept', 'sender_id': sender.id, 'receiver_id': self.owner.id})
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._handle({'action': 'accept', 'sender_id': senders[20].id, 'receiver_id': self.owner.id})
        self.assertEqual(blocked.status_code, 403, "21st POST within a minute should be rate-limited (user, 20/m)")


class ProjectInviteTests(ProjectMembershipMixin, TestCase):
    @staticmethod
    def _invite_url(project_id):
        return reverse('projects:invite-to-project', kwargs={'id': project_id})

    @staticmethod
    def _handle_invite_url(invite_id):
        return reverse('projects:handle-project-invite', kwargs={'invite_id': invite_id})

    def _invite(self, project_id, payload):
        return self.client.post(self._invite_url(project_id), data=json.dumps(payload), content_type='application/json')

    def _patch_invite(self, invite_id, payload):
        return self.client.patch(self._handle_invite_url(invite_id), data=json.dumps(payload), content_type='application/json')

    def _delete_invite(self, invite_id):
        return self.client.delete(self._handle_invite_url(invite_id))

    # ---- business logic: send ----

    def test_owner_can_invite_a_user(self):
        self.client.force_login(self.owner)
        response = self._invite(self.project.id, {'username': self.outsider.username})
        self.assertEqual(response.status_code, 201)
        invite = UserRequest.objects.get(id=response.json()['invite_id'])
        self.assertEqual(invite.sender_id, self.owner.id)
        self.assertEqual(invite.receiver_id, self.outsider.id)
        self.assertEqual(invite.request_type, 'project_invite')
        self.assertEqual(invite.status, 'pending')

    def test_non_privileged_member_cannot_invite(self):
        """'developer' has can_invite_others=False in DEFAULT_PROJECT_ROLES."""
        self.client.force_login(self.member)
        response = self._invite(self.project.id, {'username': self.outsider.username})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(UserRequest.objects.filter(request_type='project_invite').exists())

    def test_cannot_invite_self(self):
        self.client.force_login(self.owner)
        response = self._invite(self.project.id, {'username': self.owner.username})
        self.assertEqual(response.status_code, 400)

    def test_cannot_invite_existing_member(self):
        self.client.force_login(self.owner)
        response = self._invite(self.project.id, {'username': self.member.username})
        self.assertEqual(response.status_code, 400)

    def test_duplicate_invite_returns_400(self):
        self.client.force_login(self.owner)
        self._invite(self.project.id, {'username': self.outsider.username})
        second = self._invite(self.project.id, {'username': self.outsider.username})
        self.assertEqual(second.status_code, 400)
        self.assertEqual(UserRequest.objects.filter(request_type='project_invite').count(), 1)

    def test_invite_nonexistent_user_returns_404(self):
        self.client.force_login(self.owner)
        response = self._invite(self.project.id, {'username': 'this_username_was_never_registered'})
        self.assertEqual(response.status_code, 404)

    def test_invite_missing_username_returns_400(self):
        self.client.force_login(self.owner)
        response = self._invite(self.project.id, {})
        self.assertEqual(response.status_code, 400)

    def test_invite_requires_authentication(self):
        response = self._invite(self.project.id, {'username': self.outsider.username})
        self.assertEqual(response.status_code, 302)

    def test_invite_rate_limit_blocks_after_20_requests_per_user(self):
        """api_invite_to_project rate-limits POST by 'user' at 20/m."""
        targets = [make_user(f'projinviterl_{i}') for i in range(21)]
        self.client.force_login(self.owner)
        for attempt, target in enumerate(targets[:20]):
            response = self._invite(self.project.id, {'username': target.username})
            self.assertEqual(response.status_code, 201, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._invite(self.project.id, {'username': targets[20].username})
        self.assertEqual(blocked.status_code, 403, "21st POST within a minute should be rate-limited (user, 20/m)")

    # ---- business logic: handle ----

    def test_accept_invite_adds_member_with_viewer_role(self):
        """
        Regression test for two compounding real bugs:
        1) the PATCH handler used to check request.user's OWN
           can_invite_others permission before letting them accept - but the
           receiver of an invite is by definition not a project member yet
           (invites can only target visitors), so that permission was always
           False and accepting your own invite always 403'd.
        2) the role granted on acceptance referenced a nonexistent 'newbie'
           role (same bug as the join-request flow).
        """
        self.client.force_login(self.owner)
        invite_id = self._invite(self.project.id, {'username': self.outsider.username}).json()['invite_id']

        self.client.force_login(self.outsider)
        response = self._patch_invite(invite_id, {'status': 'accepted'})
        self.assertEqual(response.status_code, 200)
        role = UserProjectRole.objects.get(project=self.project, user=self.outsider)
        self.assertEqual(role.role.name, 'viewer')
        self.assertEqual(UserRequest.objects.get(id=invite_id).status, 'accepted')

    def test_decline_invite_via_delete(self):
        self.client.force_login(self.owner)
        invite_id = self._invite(self.project.id, {'username': self.outsider.username}).json()['invite_id']

        self.client.force_login(self.outsider)
        response = self._delete_invite(invite_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(UserRequest.objects.get(id=invite_id).status, 'declined')
        self.assertFalse(UserProjectRole.objects.filter(project=self.project, user=self.outsider).exists())

    def test_accept_invite_with_unsupported_status_returns_400(self):
        self.client.force_login(self.owner)
        invite_id = self._invite(self.project.id, {'username': self.outsider.username}).json()['invite_id']

        self.client.force_login(self.outsider)
        response = self._patch_invite(invite_id, {'status': 'declined'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(UserRequest.objects.get(id=invite_id).status, 'pending')

    def test_accept_already_handled_invite_returns_400(self):
        self.client.force_login(self.owner)
        invite_id = self._invite(self.project.id, {'username': self.outsider.username}).json()['invite_id']

        self.client.force_login(self.outsider)
        self._patch_invite(invite_id, {'status': 'accepted'})
        second = self._patch_invite(invite_id, {'status': 'accepted'})
        self.assertEqual(second.status_code, 400)

    def test_handle_invite_requires_authentication(self):
        response = self._patch_invite(999_999_999, {'status': 'accepted'})
        self.assertEqual(response.status_code, 302)

    def test_only_invited_user_can_accept(self):
        self.client.force_login(self.owner)
        invite_id = self._invite(self.project.id, {'username': self.outsider.username}).json()['invite_id']

        third_party = make_user('projthirdparty')
        self.client.force_login(third_party)
        response = self._patch_invite(invite_id, {'status': 'accepted'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(UserProjectRole.objects.filter(project=self.project, user=self.outsider).exists())

    def test_only_invited_user_can_decline(self):
        self.client.force_login(self.owner)
        invite_id = self._invite(self.project.id, {'username': self.outsider.username}).json()['invite_id']

        third_party = make_user('projthirdparty')
        self.client.force_login(third_party)
        response = self._delete_invite(invite_id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(UserRequest.objects.get(id=invite_id).status, 'pending')

    def test_handle_invite_rate_limit_blocks_after_20_requests_per_user(self):
        """
        api_handle_project_invite rate-limits (PATCH+DELETE share the same
        'user' key) at 20/m - keyed per user, so this needs the SAME user
        (self.outsider) handling 21 different invites, not 21 different
        users handling one each. 21 separate projects each invite
        self.outsider directly through the ORM (bypassing
        api_invite_to_project, which has its own 20/m limit that would
        otherwise trip first while just setting up fixtures).
        """
        invite_ids = []
        for i in range(21):
            other_owner = make_user(f'projinviterl_owner_{i}')
            other_project = Project.objects.create_project(other_owner.id, f'projinviterl_proj_{i}_{secrets.token_hex(4)}', 'd')
            invite_ids.append(UserRequest.objects.create(
                sender=other_owner, receiver=self.outsider, request_type='project_invite',
                target=str(other_project.id), status='pending'
            ).id)

        self.client.force_login(self.outsider)
        for attempt, invite_id in enumerate(invite_ids[:20]):
            response = self._delete_invite(invite_id)
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")

        blocked = self._delete_invite(invite_ids[20])
        self.assertEqual(blocked.status_code, 403, "21st request within a minute should be rate-limited (user, 20/m)")


class ProjectLeaveTests(ProjectMembershipMixin, TestCase):
    @staticmethod
    def _url(project_id):
        return reverse('projects:leave-project', kwargs={'id': project_id})

    def _leave(self, project_id):
        return self.client.delete(self._url(project_id))

    # ---- business logic ----

    def test_member_can_leave(self):
        self.client.force_login(self.member)
        response = self._leave(self.project.id)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserProjectRole.objects.filter(project=self.project, user=self.member).exists())

    def test_owner_leaving_transfers_ownership_to_most_active_pusher(self):
        active_member = make_user('projactive')
        developer_role = ProjectRole.objects.get(name='developer')
        UserProjectRole.objects.give_role_to_user(self.project.id, active_member.id, developer_role)
        AuditLogAction.objects.log_action(self.project, active_member, 'push')

        self.client.force_login(self.owner)
        response = self._leave(self.project.id)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserProjectRole.objects.filter(project=self.project, user=self.owner).exists())
        new_owner_role = UserProjectRole.objects.get(project=self.project, user=active_member)
        self.assertEqual(new_owner_role.role.name, 'owner')

    def test_owner_leaving_falls_back_to_longest_standing_member_when_no_pushes(self):
        self.client.force_login(self.owner)
        response = self._leave(self.project.id)
        self.assertEqual(response.status_code, 200)
        new_owner_role = UserProjectRole.objects.get(project=self.project, user=self.member)
        self.assertEqual(new_owner_role.role.name, 'owner')

    def test_sole_owner_cannot_leave(self):
        solo_owner = make_user('projsoloowner')
        solo_project = Project.objects.create_project(solo_owner.id, f'soloproj_{secrets.token_hex(4)}', 'd')
        self.client.force_login(solo_owner)
        response = self._leave(solo_project.id)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(UserProjectRole.objects.filter(project=solo_project, user=solo_owner).exists())

    def test_visitor_cannot_leave(self):
        self.client.force_login(self.outsider)
        response = self._leave(self.project.id)
        self.assertEqual(response.status_code, 403)

    def test_leave_nonexistent_project_returns_404(self):
        self.client.force_login(self.member)
        response = self._leave(self.project.id + 100_000)
        self.assertEqual(response.status_code, 404)

    # ---- security ----

    def test_leave_requires_authentication(self):
        response = self._leave(self.project.id)
        self.assertEqual(response.status_code, 302)

    # ---- rate limiting ----

    def test_rate_limit_blocks_after_20_requests_per_user(self):
        """
        api_leave_project rate-limits DELETE by 'user' at 20/m. Fixture
        projects are owned by self.owner (not self.member) - a sole owner
        can't leave their own project (see test_sole_owner_cannot_leave), so
        self.member joins each as a plain 'developer' member instead, which
        can always leave unconditionally.
        """
        developer_role = ProjectRole.objects.get(name='developer')
        projects = [
            Project.objects.create_project(self.owner.id, f'leaverl_{i}_{secrets.token_hex(4)}', 'd')
            for i in range(21)
        ]
        for proj in projects:
            UserProjectRole.objects.give_role_to_user(proj.id, self.member.id, developer_role)

        self.client.force_login(self.member)
        for attempt, proj in enumerate(projects[:20]):
            response = self._leave(proj.id)
            self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
        blocked = self._leave(projects[20].id)
        self.assertEqual(blocked.status_code, 403, "21st DELETE within a minute should be rate-limited (user, 20/m)")
class Judge0ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.outsider = make_user('projvisitor')
        self.project = Project.objects.create_project(self.owner.id, f'judge0proj_{secrets.token_hex(4)}', 'a project')

    @staticmethod
    def _url():
        return reverse('projects:run-code')

    def _post(self, payload):
        return self.client.post(self._url(), data=json.dumps(payload), content_type='application/json')

    # ---- business logic ----

    def test_run_code_success(self):
        with patch('projects.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {'stdout': 'hello\n', 'status': {'id': 3, 'description': 'Accepted'}}

            self.client.force_login(self.owner)
            response = self._post({'source_code': 'print("hello")', 'language_id': 71, 'project': self.project.name})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['stdout'], 'hello\n')

    def test_run_code_sends_the_expected_payload_and_api_key_to_judge0(self):
        """
        Verifies what our own code sends, not what Judge0 does with it - the
        whole point of mocking requests.post is that we never touch the real
        API (the free key only allows 50 runs/day), but we can still assert
        the outgoing request was built correctly.
        """
        with patch('projects.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {'stdout': 'hi\n'}

            self.client.force_login(self.owner)
            self._post({'source_code': 'print("hi")', 'language_id': 71, 'project': self.project.name})

            mock_post.assert_called_once()
            call = mock_post.call_args
            self.assertEqual(call.kwargs['json']['source_code'], 'print("hi")')
            self.assertEqual(call.kwargs['json']['language_id'], 71)
            self.assertEqual(call.kwargs['headers']['X-RapidAPI-Key'], settings.RAPIDAPI_KEY)

    def test_run_code_propagates_judge0_error_status(self):
        """No real Judge0 call happens, so this is free to run as many times as we want."""
        with patch('projects.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 429
            mock_post.return_value.json.return_value = {'message': 'Too many requests'}

            self.client.force_login(self.owner)
            response = self._post({'source_code': 'print(1)', 'language_id': 71, 'project': self.project.name})

            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.json()['message'], 'Too many requests')

    def test_missing_source_code_returns_400_without_calling_judge0(self):
        with patch('projects.views.requests.post') as mock_post:
            self.client.force_login(self.owner)
            response = self._post({'language_id': 71, 'project': self.project.name})

            self.assertEqual(response.status_code, 400)
            mock_post.assert_not_called()

    def test_missing_project_returns_400_without_calling_judge0(self):
        with patch('projects.views.requests.post') as mock_post:
            self.client.force_login(self.owner)
            response = self._post({'source_code': 'print(1)', 'language_id': 71})

            self.assertEqual(response.status_code, 400)
            mock_post.assert_not_called()

    def test_nonexistent_project_returns_404_without_calling_judge0(self):
        with patch('projects.views.requests.post') as mock_post:
            self.client.force_login(self.owner)
            response = self._post({'source_code': 'print(1)', 'language_id': 71, 'project': 'this_project_was_never_created'})

            self.assertEqual(response.status_code, 404)
            mock_post.assert_not_called()

    # ---- security ----

    def test_requires_authentication(self):
        response = self._post({'source_code': 'print(1)', 'language_id': 71, 'project': self.project.name})
        self.assertEqual(response.status_code, 302)

    def test_visitor_cannot_run_code(self):
        with patch('projects.views.requests.post') as mock_post:
            self.client.force_login(self.outsider)
            response = self._post({'source_code': 'print(1)', 'language_id': 71, 'project': self.project.name})

            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()

    def test_member_without_can_execute_code_permission_is_rejected(self):
        member = make_user('projmember')
        developer_role = ProjectRole.objects.get(name='developer')  # can_execute_code=False by default
        UserProjectRole.objects.give_role_to_user(self.project.id, member.id, developer_role)

        with patch('projects.views.requests.post') as mock_post:
            self.client.force_login(member)
            response = self._post({'source_code': 'print(1)', 'language_id': 71, 'project': self.project.name})

            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()

    # ---- rate limiting ----

    def test_rate_limit_blocks_after_20_requests_per_user(self):
        """proxy_run_code rate-limits POST by 'user' at 20/m."""
        with patch('projects.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {'stdout': 'ok\n'}

            self.client.force_login(self.owner)
            for attempt in range(20):
                response = self._post({'source_code': f'print({attempt})', 'language_id': 71, 'project': self.project.name})
                self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")

            blocked = self._post({'source_code': 'print("blocked")', 'language_id': 71, 'project': self.project.name})
            self.assertEqual(blocked.status_code, 403, "21st POST within a minute should be rate-limited (user, 20/m)")


class GitHubSignatureVerificationTests(TestCase):
    """
    verify_github_signature is a pure function (no requests.post involved) -
    it's what stands between webhook_github and anyone on the internet who
    knows a project's webhook URL and wants to forge a push event, so it's
    worth testing thoroughly with zero mocking needed.
    """

    def test_valid_signature_returns_true(self):
        secret = 'my-webhook-secret'
        body = b'{"some":"payload"}'
        signature = 'sha256=' + hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_github_signature(body, signature, secret))

    def test_invalid_signature_returns_false(self):
        body = b'{"some":"payload"}'
        self.assertFalse(verify_github_signature(body, 'sha256=' + '0' * 64, 'my-webhook-secret'))

    def test_missing_signature_header_returns_false(self):
        self.assertFalse(verify_github_signature(b'{}', None, 'my-webhook-secret'))

    def test_empty_signature_header_returns_false(self):
        self.assertFalse(verify_github_signature(b'{}', '', 'my-webhook-secret'))

    def test_signature_without_sha256_prefix_returns_false(self):
        """GitHub always sends sha256=... on X-Hub-Signature-256 - a bare hex digest (or a sha1= one) must be rejected."""
        secret = 'my-webhook-secret'
        body = b'{"some":"payload"}'
        bare_hex = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
        self.assertFalse(verify_github_signature(body, bare_hex, secret))
        self.assertFalse(verify_github_signature(body, 'sha1=' + bare_hex, secret))

    def test_signature_computed_with_the_wrong_secret_returns_false(self):
        body = b'{"some":"payload"}'
        signature = 'sha256=' + hmac.new(b'attackers-guess', body, hashlib.sha256).hexdigest()
        self.assertFalse(verify_github_signature(body, signature, 'my-webhook-secret'))

    def test_tampering_with_the_payload_after_signing_invalidates_it(self):
        secret = 'my-webhook-secret'
        original_body = b'{"amount": 10}'
        signature = 'sha256=' + hmac.new(secret.encode('utf-8'), original_body, hashlib.sha256).hexdigest()
        tampered_body = b'{"amount": 10000}'
        self.assertFalse(verify_github_signature(tampered_body, signature, secret))


class CommitProvenanceTests(TestCase):
    """
    commit_was_pushed_from_app is the other pure-function security check:
    given a push webhook was legitimately signed by the right repo secret
    (see GitHubSignatureVerificationTests above), this decides whether each
    individual COMMIT inside it actually came from push_files() (our own
    HMAC trailer) or was pushed some other way (manual git push, GitHub UI
    edit) while can_only_modify_from_app is enabled.
    """

    @staticmethod
    def _signed_message(secret, body_text):
        signature = hmac.new(secret.encode('utf-8'), body_text.encode('utf-8'), hashlib.sha256).hexdigest()
        return f'{body_text}\n\nX-GitSync-Sig: {signature}'

    def test_commit_with_a_valid_trailer_is_recognized_as_from_the_app(self):
        secret = 'app-signing-key'
        commit = {'message': self._signed_message(secret, 'Pushed via GitSync: update readme | README.md | 2025-01-01T00:00:00')}
        self.assertTrue(commit_was_pushed_from_app(commit, secret))

    def test_commit_without_a_trailer_is_not_from_the_app(self):
        commit = {'message': 'a normal manual commit message, no trailer at all'}
        self.assertFalse(commit_was_pushed_from_app(commit, 'app-signing-key'))

    def test_commit_missing_the_message_key_is_not_from_the_app(self):
        self.assertFalse(commit_was_pushed_from_app({}, 'app-signing-key'))

    def test_commit_with_a_tampered_trailer_is_not_from_the_app(self):
        secret = 'app-signing-key'
        real = self._signed_message(secret, 'legit message body')
        flipped_last_char = '1' if real[-1] != '1' else '2'
        tampered = real[:-1] + flipped_last_char
        self.assertFalse(commit_was_pushed_from_app({'message': tampered}, secret))

    def test_commit_signed_with_a_different_secret_is_not_from_the_app(self):
        commit = {'message': self._signed_message('some-other-projects-secret', 'message body')}
        self.assertFalse(commit_was_pushed_from_app(commit, 'app-signing-key'))

    def test_commit_message_body_was_altered_after_signing_is_not_from_the_app(self):
        """The trailer is valid for a DIFFERENT message than the one actually present - i.e. the message was edited post-signing."""
        secret = 'app-signing-key'
        signed = self._signed_message(secret, 'original message')
        tampered = signed.replace('original message', 'edited message')
        self.assertFalse(commit_was_pushed_from_app({'message': tampered}, secret))


class WebhookGithubTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.project = Project.objects.create_project(self.owner.id, f'webhookproj_{secrets.token_hex(4)}', 'd')
        self.repo_stat = ProjectRepoStats.objects.create(
            github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets', github_token=''
        )
        self.project.repo_stats.add(self.repo_stat)

    @staticmethod
    def _url(project_id):
        return reverse('projects:webhook-github', kwargs={'id': project_id})

    @staticmethod
    def _signed_commit_message(secret, body_text):
        signature = hmac.new(secret.encode('utf-8'), body_text.encode('utf-8'), hashlib.sha256).hexdigest()
        return f'{body_text}\n\nX-GitSync-Sig: {signature}'

    @staticmethod
    def _payload(commits=None, full_name='acme/widgets'):
        return {'repository': {'full_name': full_name}, 'commits': commits or []}

    def _signed_post(self, project_id, payload, secret=None):
        secret = secret if secret is not None else self.project.app_signing_key
        raw_body = json.dumps(payload).encode('utf-8')
        signature = 'sha256=' + hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
        return self.client.post(
            self._url(project_id), data=raw_body, content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature
        )

    # ---- business logic ----

    def test_missing_repository_info_returns_400(self):
        response = self._signed_post(self.project.id, {'commits': []})
        self.assertEqual(response.status_code, 400)

    def test_untracked_repo_returns_400(self):
        response = self._signed_post(self.project.id, self._payload(full_name='someone/else'))
        self.assertEqual(response.status_code, 400)

    def test_nonexistent_project_returns_404(self):
        response = self._signed_post(self.project.id + 100_000, self._payload())
        self.assertEqual(response.status_code, 404)

    def test_when_push_policy_disabled_nothing_is_enforced(self):
        """can_only_modify_from_app is False by default - an unsigned commit shouldn't get flagged."""
        response = self._signed_post(self.project.id, self._payload(commits=[{'message': 'anything, unsigned'}]))
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertFalse(self.project.flagged_external_push)

    def test_all_commits_from_app_does_not_flag(self):
        self.project.can_only_modify_from_app = True
        self.project.save(update_fields=['can_only_modify_from_app'])
        commit = {'message': self._signed_commit_message(self.project.app_signing_key, 'legit push | file.py | 2025-01-01')}

        response = self._signed_post(self.project.id, self._payload(commits=[commit]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['flagged_external_push'])
        self.project.refresh_from_db()
        self.assertFalse(self.project.flagged_external_push)

    def test_commit_not_from_app_flags_the_project(self):
        self.project.can_only_modify_from_app = True
        self.project.save(update_fields=['can_only_modify_from_app'])
        commit = {'message': 'a manual git push, no GitSync trailer'}

        response = self._signed_post(self.project.id, self._payload(commits=[commit]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['flagged_external_push'])
        self.project.refresh_from_db()
        self.assertTrue(self.project.flagged_external_push)

    # ---- security ----

    def test_invalid_signature_is_rejected(self):
        response = self._signed_post(self.project.id, self._payload(), secret='wrong-secret')
        self.assertEqual(response.status_code, 403)

    def test_missing_signature_header_is_rejected(self):
        raw_body = json.dumps(self._payload()).encode('utf-8')
        response = self.client.post(self._url(self.project.id), data=raw_body, content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_payload_tampered_with_after_signing_is_rejected(self):
        secret = self.project.app_signing_key
        raw_body = json.dumps(self._payload()).encode('utf-8')
        signature = 'sha256=' + hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
        tampered_body = json.dumps(self._payload(commits=[{'message': 'sneaky extra commit'}])).encode('utf-8')

        response = self.client.post(
            self._url(self.project.id), data=tampered_body, content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature
        )
        self.assertEqual(response.status_code, 403)

    def test_url_project_id_is_respected_even_when_a_repo_is_shared_between_two_projects(self):
        """
        Regression test for a real bug: webhook_github used to re-look-up
        the repo_stat with a GLOBAL, unscoped query and reassign
        `project = repo_stat.projects.first()`. Since ProjectRepoStats can
        be shared by several projects (Project.repo_stats is M2M), a repo
        linked to two projects meant every webhook always resolved to
        whichever project got linked first, ignoring the URL's own id for
        the signature check and the flagging that followed. Now the
        already-validated `project` from the URL is used throughout, so a
        webhook aimed at project B, signed with B's own key, is checked and
        flagged against B - regardless of insertion order or what A's key is.
        """
        other_project = Project.objects.create_project(self.owner.id, f'webhookproj_other_{secrets.token_hex(4)}', 'd')
        other_project.repo_stats.add(self.repo_stat)  # same repo_stat, now shared by self.project and other_project
        other_project.can_only_modify_from_app = True
        other_project.save(update_fields=['can_only_modify_from_app'])

        secret = other_project.app_signing_key
        raw_body = json.dumps(self._payload(commits=[{'message': 'unsigned manual push'}])).encode('utf-8')
        signature = 'sha256=' + hmac.new(secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
        response = self.client.post(
            self._url(other_project.id), data=raw_body, content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=signature
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['flagged_external_push'])

        other_project.refresh_from_db()
        self.assertTrue(other_project.flagged_external_push, "the project addressed by the URL must be the one flagged")
        self.project.refresh_from_db()
        self.assertFalse(self.project.flagged_external_push, "the OTHER project sharing the repo must be untouched")


def github_get_router(**url_suffix_to_response):
    """
    Builds a requests.get side_effect for mocking projects.github_utils.requests.get,
    which is called by several different helper functions hitting different GitHub
    endpoints (repo metadata, branches list, branch protection, refs/heads/<branch>) in
    the same request - a single fixed return_value can't serve all of them. Keys are
    matched against the end of the requested URL; values are (status_code, json_body).
    """
    def side_effect(url, *args, **kwargs):
        for suffix, (status_code, json_body) in url_suffix_to_response.items():
            if url.endswith(suffix):
                resp = Mock()
                resp.status_code = status_code
                resp.ok = 200 <= status_code < 300
                resp.json.return_value = json_body
                return resp
        raise AssertionError(f"Unexpected GET url in test, no mock configured for it: {url}")
    return side_effect


class GithubBranchListingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.outsider = make_user('projvisitor')
        self.project = Project.objects.create_project(self.owner.id, f'branchlistproj_{secrets.token_hex(4)}', 'd')
        self.repo_stat = ProjectRepoStats.objects.create(
            github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets', github_token=''
        )
        self.project.repo_stats.add(self.repo_stat)

    @staticmethod
    def _url():
        return reverse('projects:get-all-repo-branches')

    def _get(self, params):
        return self.client.get(self._url(), params)

    @staticmethod
    def _router(is_private=False, branches=('main',)):
        return github_get_router(**{
            '/acme/widgets': (200, {'visibility': 'private' if is_private else 'public'}),
            '/branches': (200, [{'name': b} for b in branches]),
        })

    # ---- business logic ----

    def test_lists_branches_for_a_public_repo(self):
        with patch('projects.github_utils.requests.get') as mock_get:
            mock_get.side_effect = self._router(is_private=False, branches=['main', 'dev'])
            self.client.force_login(self.owner)
            response = self._get({'project': self.project.name})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['branches'], ['main', 'dev'])

    def test_lists_branches_for_a_private_repo(self):
        """Exercises the is_repo_private()==True branch, which sends an Authorization header on the branches call too."""
        with patch('projects.github_utils.requests.get') as mock_get:
            mock_get.side_effect = self._router(is_private=True, branches=['main'])
            self.client.force_login(self.owner)
            response = self._get({'project': self.project.name})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['branches'], ['main'])

    def test_missing_project_name_does_not_call_github(self):
        with patch('projects.github_utils.requests.get') as mock_get:
            self.client.force_login(self.owner)
            response = self._get({})
            self.assertIn(response.status_code, (400, 403))
            mock_get.assert_not_called()

    def test_nonexistent_project_returns_404_without_calling_github(self):
        with patch('projects.github_utils.requests.get') as mock_get:
            self.client.force_login(self.owner)
            response = self._get({'project': 'this_project_was_never_created'})
            self.assertEqual(response.status_code, 404)
            mock_get.assert_not_called()

    def test_repo_not_linked_to_project_does_not_call_github(self):
        with patch('projects.github_utils.requests.get') as mock_get:
            self.client.force_login(self.owner)
            response = self._get({'project': self.project.name, 'repo_id': 999_999})
            self.assertNotEqual(response.status_code, 200)
            mock_get.assert_not_called()

    def test_github_failure_returns_500(self):
        """No real GitHub outage needed to test this - we just make the mock fail."""
        with patch('projects.github_utils.requests.get') as mock_get:
            mock_get.side_effect = github_get_router(**{
                '/acme/widgets': (200, {'visibility': 'public'}),
                '/branches': (404, {'message': 'Not Found'}),
            })
            self.client.force_login(self.owner)
            response = self._get({'project': self.project.name})
            self.assertEqual(response.status_code, 500)

    # ---- security ----

    def test_requires_authentication(self):
        response = self._get({'project': self.project.name})
        self.assertEqual(response.status_code, 302)

    def test_outsider_can_view_branches_with_no_membership_check(self):
        """
        Documents current behavior, not a requirement: unlike
        _get_project_domains/_get_project_requirements/_get_project_push_policy
        (which we already locked down to require membership),
        api_github_get_all_repo_branches still has no role check at all.
        """
        with patch('projects.github_utils.requests.get') as mock_get:
            mock_get.side_effect = self._router()
            self.client.force_login(self.outsider)
            response = self._get({'project': self.project.name})
            self.assertEqual(response.status_code, 200)

    # ---- rate limiting ----

    def test_rate_limit_blocks_after_60_requests_per_user(self):
        """api_github_get_all_repo_branches rate-limits GET by 'user' at 60/m."""
        with patch('projects.github_utils.requests.get') as mock_get:
            mock_get.side_effect = self._router()
            self.client.force_login(self.owner)
            for attempt in range(60):
                response = self._get({'project': self.project.name})
                self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
            blocked = self._get({'project': self.project.name})
            self.assertEqual(blocked.status_code, 403, "61st GET within a minute should be rate-limited (user, 60/m)")


class GithubBranchActionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.member = make_user('projmember')
        self.outsider = make_user('projvisitor')
        self.project = Project.objects.create_project(self.owner.id, f'branchactionproj_{secrets.token_hex(4)}', 'd')
        self.repo_stat = ProjectRepoStats.objects.create(
            github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets', github_token=''
        )
        self.project.repo_stats.add(self.repo_stat)
        developer_role = ProjectRole.objects.get(name='developer')
        UserProjectRole.objects.give_role_to_user(self.project.id, self.member.id, developer_role)

    @staticmethod
    def _url(project_id):
        return reverse('projects:add-branch-on-github-repo', kwargs={'id': project_id})

    def _post(self, project_id):
        return self.client.post(self._url(project_id))

    def _put(self, project_id, payload):
        return self.client.put(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    def _delete(self, project_id, payload):
        return self.client.delete(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    # ---- POST: add a new branch ----

    def test_owner_can_add_a_branch(self):
        with patch('projects.github_utils.requests.get') as mock_get, \
             patch('projects.github_utils.requests.post') as mock_post:
            mock_get.side_effect = github_get_router(**{
                '/acme/widgets': (200, {'default_branch': 'main'}),
                '/git/refs/heads/main': (200, {'object': {'sha': 'abc123'}}),
            })
            mock_post.return_value = Mock(status_code=201)

            self.client.force_login(self.owner)
            response = self._post(self.project.id)
            self.assertEqual(response.status_code, 200)
            mock_post.assert_called_once()
            self.assertEqual(mock_post.call_args.kwargs['json']['sha'], 'abc123')

    def test_add_branch_github_failure_is_reported(self):
        with patch('projects.github_utils.requests.get') as mock_get, \
             patch('projects.github_utils.requests.post') as mock_post:
            mock_get.side_effect = github_get_router(**{
                '/acme/widgets': (200, {'default_branch': 'main'}),
                '/git/refs/heads/main': (200, {'object': {'sha': 'abc123'}}),
            })
            mock_post.return_value = Mock(status_code=422, json=lambda: {'message': 'Reference already exists'})

            self.client.force_login(self.owner)
            response = self._post(self.project.id)
            self.assertNotEqual(response.status_code, 200)

    def test_non_privileged_member_cannot_add_a_branch(self):
        """'developer' has can_create_branches=True, so use a role without it - viewer."""
        viewer = make_user('projviewer')
        viewer_role = ProjectRole.objects.get(name='viewer')
        UserProjectRole.objects.give_role_to_user(self.project.id, viewer.id, viewer_role)

        with patch('projects.github_utils.requests.post') as mock_post:
            self.client.force_login(viewer)
            response = self._post(self.project.id)
            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()

    def test_outsider_cannot_add_a_branch(self):
        with patch('projects.github_utils.requests.post') as mock_post:
            self.client.force_login(self.outsider)
            response = self._post(self.project.id)
            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()

    def test_add_branch_requires_authentication(self):
        response = self._post(self.project.id)
        self.assertEqual(response.status_code, 302)

    # ---- PUT: rename a branch ----

    def test_owner_can_rename_a_branch(self):
        """
        Regression test for a real bug: this used to check
        visitor_permissions['can_modify_branches'], a key that didn't exist
        anywhere (not on the ProjectRole model, not in get_role_permissions'
        permission_keys) - every PUT request raised an uncaught KeyError.
        """
        with patch('projects.github_utils.requests.post') as mock_post:
            mock_post.return_value = Mock(status_code=201)
            self.client.force_login(self.owner)
            response = self._put(self.project.id, {'branch_name': 'old-name', 'new_name': 'new-name'})
            self.assertEqual(response.status_code, 200)
            mock_post.assert_called_once()

    def test_rename_branch_missing_fields_returns_400(self):
        with patch('projects.github_utils.requests.post') as mock_post:
            self.client.force_login(self.owner)
            response = self._put(self.project.id, {'branch_name': 'old-name'})
            self.assertEqual(response.status_code, 400)
            mock_post.assert_not_called()

    def test_non_privileged_member_cannot_rename_a_branch(self):
        viewer = make_user('projviewer')
        viewer_role = ProjectRole.objects.get(name='viewer')
        UserProjectRole.objects.give_role_to_user(self.project.id, viewer.id, viewer_role)

        with patch('projects.github_utils.requests.post') as mock_post:
            self.client.force_login(viewer)
            response = self._put(self.project.id, {'branch_name': 'old-name', 'new_name': 'new-name'})
            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()

    # ---- DELETE: delete a branch ----

    def test_owner_can_delete_a_branch(self):
        with patch('projects.github_utils.requests.get') as mock_get, \
             patch('projects.github_utils.requests.delete') as mock_delete:
            mock_get.side_effect = github_get_router(**{'/acme/widgets': (200, {'default_branch': 'main'})})
            mock_delete.return_value = Mock(status_code=204, content=b'')

            self.client.force_login(self.owner)
            response = self._delete(self.project.id, {'branch_name': 'feature-branch'})
            self.assertEqual(response.status_code, 200)
            mock_delete.assert_called_once()

    def test_cannot_delete_the_default_branch(self):
        with patch('projects.github_utils.requests.get') as mock_get, \
             patch('projects.github_utils.requests.delete') as mock_delete:
            mock_get.side_effect = github_get_router(**{'/acme/widgets': (200, {'default_branch': 'main'})})

            self.client.force_login(self.owner)
            response = self._delete(self.project.id, {'branch_name': 'main'})
            self.assertEqual(response.status_code, 400)
            mock_delete.assert_not_called()

    def test_non_privileged_member_cannot_delete_a_branch(self):
        with patch('projects.github_utils.requests.get') as mock_get, \
             patch('projects.github_utils.requests.delete') as mock_delete:
            self.client.force_login(self.member)  # 'developer' has can_delete_branches=False
            response = self._delete(self.project.id, {'branch_name': 'feature-branch'})
            self.assertEqual(response.status_code, 403)
            mock_get.assert_not_called()
            mock_delete.assert_not_called()

    # ---- rate limiting ----

    def test_rate_limit_blocks_after_15_requests_per_user(self):
        """api_github_handle_branch_action rate-limits (POST/PUT/DELETE share the same 'user' key) at 15/m."""
        with patch('projects.github_utils.requests.get') as mock_get, \
             patch('projects.github_utils.requests.post') as mock_post:
            mock_get.side_effect = github_get_router(**{
                '/acme/widgets': (200, {'default_branch': 'main'}),
                '/git/refs/heads/main': (200, {'object': {'sha': 'abc123'}}),
            })
            mock_post.return_value = Mock(status_code=201)

            self.client.force_login(self.owner)
            for attempt in range(15):
                response = self._post(self.project.id)
                self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
            blocked = self._post(self.project.id)
            self.assertEqual(blocked.status_code, 403, "16th request within a minute should be rate-limited (user, 15/m)")


class GithubMergeBranchesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.outsider = make_user('projvisitor')
        self.project = Project.objects.create_project(self.owner.id, f'mergeproj_{secrets.token_hex(4)}', 'd')
        self.repo_stat = ProjectRepoStats.objects.create(
            github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets', github_token=''
        )
        self.project.repo_stats.add(self.repo_stat)

    @staticmethod
    def _url(project_id):
        return reverse('projects:merge-branches', kwargs={'id': project_id})

    def _post(self, project_id, payload):
        return self.client.post(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    # ---- business logic ----

    def test_owner_can_merge_branches(self):
        with patch('projects.views.requests.post') as mock_post:
            mock_post.return_value = Mock(status_code=201)
            self.client.force_login(self.owner)
            response = self._post(self.project.id, {'base': 'main', 'head': 'feature'})
            self.assertEqual(response.status_code, 200)
            mock_post.assert_called_once()
            self.assertEqual(mock_post.call_args.kwargs['json']['base'], 'main')
            self.assertEqual(mock_post.call_args.kwargs['json']['head'], 'feature')

    def test_missing_base_or_head_does_not_call_github(self):
        with patch('projects.views.requests.post') as mock_post:
            self.client.force_login(self.owner)
            response = self._post(self.project.id, {'base': 'main'})
            self.assertNotEqual(response.status_code, 200)
            mock_post.assert_not_called()

    def test_merge_conflict_is_reported(self):
        with patch('projects.views.requests.post') as mock_post:
            mock_post.return_value = Mock(status_code=409)
            self.client.force_login(self.owner)
            response = self._post(self.project.id, {'base': 'main', 'head': 'feature'})
            self.assertEqual(response.status_code, 409)

    def test_nothing_to_merge_is_reported(self):
        mock_post_response = Mock(status_code=204)
        with patch('projects.views.requests.post', return_value=mock_post_response):
            self.client.force_login(self.owner)
            response = self._post(self.project.id, {'base': 'main', 'head': 'feature'})
            self.assertEqual(response.status_code, 204)

    # ---- security ----

    def test_requires_authentication(self):
        response = self._post(self.project.id, {'base': 'main', 'head': 'feature'})
        self.assertEqual(response.status_code, 302)

    def test_non_privileged_member_cannot_merge(self):
        member = make_user('projmember')
        developer_role = ProjectRole.objects.get(name='developer')  # can_merge_branches=False
        UserProjectRole.objects.give_role_to_user(self.project.id, member.id, developer_role)

        with patch('projects.views.requests.post') as mock_post:
            self.client.force_login(member)
            response = self._post(self.project.id, {'base': 'main', 'head': 'feature'})
            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()

    def test_outsider_cannot_merge(self):
        with patch('projects.views.requests.post') as mock_post:
            self.client.force_login(self.outsider)
            response = self._post(self.project.id, {'base': 'main', 'head': 'feature'})
            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()

    # ---- rate limiting ----

    def test_rate_limit_blocks_after_15_requests_per_user(self):
        """api_merge_github_branches rate-limits POST by 'user' at 15/m."""
        with patch('projects.views.requests.post') as mock_post:
            mock_post.return_value = Mock(status_code=201)
            self.client.force_login(self.owner)
            for attempt in range(15):
                response = self._post(self.project.id, {'base': 'main', 'head': f'feature{attempt}'})
                self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
            blocked = self._post(self.project.id, {'base': 'main', 'head': 'feature-blocked'})
            self.assertEqual(blocked.status_code, 403, "16th POST within a minute should be rate-limited (user, 15/m)")


class ProjectRepositoryLinkingTests(TestCase):
    """
    _add_project_repository / _delete_project_repository - the most involved
    mocking in the GitHub suite: adding a repo can chain a webhook
    registration (POST) with, if the project's push policy is on, branch
    protection (GET default branch, GET existing protection, GET github
    username, PUT protection). Removing a protected repo chains its own
    revert (DELETE or PUT depending on whether protection pre-existed).
    """

    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.member = make_user('projmember')
        self.outsider = make_user('projvisitor')
        self.project = Project.objects.create_project(self.owner.id, f'repolinkproj_{secrets.token_hex(4)}', 'd')
        developer_role = ProjectRole.objects.get(name='developer')  # can_change_project_settings=False
        UserProjectRole.objects.give_role_to_user(self.project.id, self.member.id, developer_role)

    @staticmethod
    def _url(project_id):
        return reverse('projects:add-repo-to-project', kwargs={'id': project_id})

    def _post(self, project_id, payload):
        return self.client.post(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    def _delete(self, project_id, payload):
        return self.client.delete(self._url(project_id), data=json.dumps(payload), content_type='application/json')

    @staticmethod
    def _repo_payload(**overrides):
        payload = {
            'github_repo_name': 'widgets',
            'github_repo_link': 'https://github.com/acme/widgets',
            'github_token': '',
        }
        payload.update(overrides)
        return payload

    # ---- POST: add a repo, push policy off (no branch protection attempted) ----

    def test_owner_can_add_a_repo_when_push_policy_is_off(self):
        with patch('projects.github_utils.requests.post') as mock_post:
            mock_post.return_value = Mock(status_code=201)
            self.client.force_login(self.owner)
            response = self._post(self.project.id, self._repo_payload())

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data['webhook_registered'])
            self.assertFalse(data['branch_protection_applied'], "can_only_modify_from_app is False by default - protection shouldn't be attempted")

            repo_stat = ProjectRepoStats.objects.get(id=data['repo_id'])
            self.assertEqual(repo_stat.github_repo_link, 'https://github.com/acme/widgets')
            self.assertIn(repo_stat, self.project.repo_stats.all())

    def test_webhook_registration_failure_does_not_block_linking_the_repo(self):
        """register_github_webhook is best-effort - a repo is still linked even if GitHub rejects the webhook."""
        with patch('projects.github_utils.requests.post') as mock_post:
            mock_post.return_value = Mock(status_code=422)  # GitHub rejected it
            self.client.force_login(self.owner)
            response = self._post(self.project.id, self._repo_payload())

            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['webhook_registered'])
            self.assertTrue(ProjectRepoStats.objects.filter(id=response.json()['repo_id']).exists())

    def test_response_never_echoes_the_access_token(self):
        with patch('projects.github_utils.requests.post') as mock_post:
            mock_post.return_value = Mock(status_code=201)
            self.client.force_login(self.owner)
            response = self._post(self.project.id, self._repo_payload(github_token='ghp_supersecrettoken'))

            self.assertNotIn('ghp_supersecrettoken', response.content.decode())

    def test_missing_required_fields_returns_400(self):
        with patch('projects.github_utils.requests.post') as mock_post:
            self.client.force_login(self.owner)
            response = self._post(self.project.id, {'github_token': 'x'})
            self.assertEqual(response.status_code, 400)
            mock_post.assert_not_called()

    # ---- POST: add a repo, push policy on (branch protection is attempted too) ----

    def test_add_repo_with_push_policy_on_also_applies_branch_protection(self):
        self.project.can_only_modify_from_app = True
        self.project.save(update_fields=['can_only_modify_from_app'])

        with patch('projects.github_utils.requests.get') as mock_get, \
             patch('projects.github_utils.requests.post') as mock_post, \
             patch('projects.github_utils.requests.put') as mock_put:
            mock_get.side_effect = github_get_router(**{
                '/acme/widgets': (200, {'default_branch': 'main'}),
                '/branches/main/protection': (404, {'message': 'Branch not protected'}),
                '/user': (200, {'login': 'app-bot'}),
            })
            mock_post.return_value = Mock(status_code=201)  # webhook registration
            mock_put.return_value = Mock(status_code=200)  # branch protection PUT

            self.client.force_login(self.owner)
            response = self._post(self.project.id, self._repo_payload())

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data['webhook_registered'])
            self.assertTrue(data['branch_protection_applied'])

            repo_stat = ProjectRepoStats.objects.get(id=data['repo_id'])
            self.assertEqual(repo_stat.protected_branch, 'main')

    def test_branch_protection_failure_is_reported_but_repo_stays_linked(self):
        self.project.can_only_modify_from_app = True
        self.project.save(update_fields=['can_only_modify_from_app'])

        with patch('projects.github_utils.requests.get') as mock_get, \
             patch('projects.github_utils.requests.post') as mock_post, \
             patch('projects.github_utils.requests.put') as mock_put:
            mock_get.side_effect = github_get_router(**{
                '/acme/widgets': (200, {'default_branch': 'main'}),
                '/branches/main/protection': (404, {'message': 'Branch not protected'}),
                '/user': (200, {'login': 'app-bot'}),
            })
            mock_post.return_value = Mock(status_code=201)
            mock_put.return_value = Mock(status_code=403)  # GitHub refuses (e.g. token lacks admin rights)

            self.client.force_login(self.owner)
            response = self._post(self.project.id, self._repo_payload())

            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['branch_protection_applied'])
            self.assertTrue(ProjectRepoStats.objects.filter(id=response.json()['repo_id']).exists())

    # ---- POST: security ----

    def test_add_repo_requires_authentication(self):
        response = self._post(self.project.id, self._repo_payload())
        self.assertEqual(response.status_code, 302)

    def test_non_privileged_member_cannot_add_a_repo(self):
        with patch('projects.github_utils.requests.post') as mock_post:
            self.client.force_login(self.member)  # 'developer': can_change_project_settings=False
            response = self._post(self.project.id, self._repo_payload())
            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()
            self.assertFalse(ProjectRepoStats.objects.exists())

    def test_outsider_cannot_add_a_repo(self):
        with patch('projects.github_utils.requests.post') as mock_post:
            self.client.force_login(self.outsider)
            response = self._post(self.project.id, self._repo_payload())
            self.assertEqual(response.status_code, 403)
            mock_post.assert_not_called()

    # ---- DELETE: remove an unprotected repo (no GitHub calls at all) ----

    def test_owner_can_remove_an_unprotected_repo(self):
        repo_stat = ProjectRepoStats.objects.create(github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets')
        self.project.repo_stats.add(repo_stat)

        with patch('projects.github_utils.requests.delete') as mock_delete, \
             patch('projects.github_utils.requests.put') as mock_put:
            self.client.force_login(self.owner)
            response = self._delete(self.project.id, {'repo_id': repo_stat.id})

            self.assertEqual(response.status_code, 200)
            self.assertFalse(
                self.project.repo_stats.filter(id=repo_stat.id).exists(),
                "repo_stats.remove() only unlinks the M2M row, it doesn't delete ProjectRepoStats itself"
            )
            mock_delete.assert_not_called()
            mock_put.assert_not_called()

    def test_delete_missing_repo_id_returns_400(self):
        self.client.force_login(self.owner)
        response = self._delete(self.project.id, {})
        self.assertEqual(response.status_code, 400)

    def test_delete_repo_not_linked_to_this_project_returns_404(self):
        unlinked_repo = ProjectRepoStats.objects.create(github_repo_name='other', github_repo_link='https://github.com/acme/other')
        self.client.force_login(self.owner)
        response = self._delete(self.project.id, {'repo_id': unlinked_repo.id})
        self.assertEqual(response.status_code, 404)

    # ---- DELETE: remove a protected repo (reverts branch protection first) ----

    def test_removing_a_previously_unprotected_branch_deletes_the_protection(self):
        """previous_branch_protection is empty - it was protected by US, with nothing to restore, so DELETE removes it outright."""
        repo_stat = ProjectRepoStats.objects.create(
            github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets',
            protected_branch='main', previous_branch_protection='',
        )
        self.project.repo_stats.add(repo_stat)

        with patch('projects.github_utils.requests.delete') as mock_delete:
            mock_delete.return_value = Mock(status_code=204)
            self.client.force_login(self.owner)
            response = self._delete(self.project.id, {'repo_id': repo_stat.id})

            self.assertEqual(response.status_code, 200)
            mock_delete.assert_called_once()
            self.assertFalse(
                self.project.repo_stats.filter(id=repo_stat.id).exists(),
                "repo_stats.remove() only unlinks the M2M row, it doesn't delete ProjectRepoStats itself"
            )

    def test_removing_a_previously_protected_branch_restores_the_old_protection(self):
        """previous_branch_protection has a saved snapshot - revert PUTs it back instead of deleting protection outright."""
        repo_stat = ProjectRepoStats.objects.create(
            github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets',
            protected_branch='main',
            previous_branch_protection=json.dumps({
                'required_status_checks': None,
                'enforce_admins': {'enabled': True},
                'required_pull_request_reviews': {'required_approving_review_count': 2},
                'restrictions': {'users': [{'login': 'someone'}], 'teams': [], 'apps': []},
            }),
        )
        self.project.repo_stats.add(repo_stat)

        with patch('projects.github_utils.requests.put') as mock_put:
            mock_put.return_value = Mock(status_code=200)
            self.client.force_login(self.owner)
            response = self._delete(self.project.id, {'repo_id': repo_stat.id})

            self.assertEqual(response.status_code, 200)
            mock_put.assert_called_once()
            self.assertEqual(mock_put.call_args.kwargs['json']['restrictions']['users'], ['someone'])
            self.assertFalse(
                self.project.repo_stats.filter(id=repo_stat.id).exists(),
                "repo_stats.remove() only unlinks the M2M row, it doesn't delete ProjectRepoStats itself"
            )

    # ---- DELETE: security ----

    def test_delete_repo_requires_authentication(self):
        repo_stat = ProjectRepoStats.objects.create(github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets')
        self.project.repo_stats.add(repo_stat)
        response = self._delete(self.project.id, {'repo_id': repo_stat.id})
        self.assertEqual(response.status_code, 302)

    def test_non_privileged_member_cannot_remove_a_repo(self):
        repo_stat = ProjectRepoStats.objects.create(github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets')
        self.project.repo_stats.add(repo_stat)

        self.client.force_login(self.member)
        response = self._delete(self.project.id, {'repo_id': repo_stat.id})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ProjectRepoStats.objects.filter(id=repo_stat.id).exists())

    def test_outsider_cannot_remove_a_repo(self):
        repo_stat = ProjectRepoStats.objects.create(github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets')
        self.project.repo_stats.add(repo_stat)

        self.client.force_login(self.outsider)
        response = self._delete(self.project.id, {'repo_id': repo_stat.id})
        self.assertEqual(response.status_code, 403)

    # ---- rate limiting ----

    def test_rate_limit_blocks_after_30_requests_per_user(self):
        """api_handle_project_repositories rate-limits (POST+DELETE share the same 'user' key) at 30/m."""
        with patch('projects.github_utils.requests.post') as mock_post:
            mock_post.return_value = Mock(status_code=201)
            self.client.force_login(self.owner)
            for attempt in range(30):
                response = self._post(self.project.id, self._repo_payload(github_repo_name=f'repo{attempt}'))
                self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
            blocked = self._post(self.project.id, self._repo_payload(github_repo_name='repo-blocked'))
            self.assertEqual(blocked.status_code, 403, "31st request within a minute should be rate-limited (user, 30/m)")


class GithubProxyViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.outsider = make_user('projvisitor')
        self.project = Project.objects.create_project(self.owner.id, f'proxyviewproj_{secrets.token_hex(4)}', 'd')
        self.repo_stat = ProjectRepoStats.objects.create(
            github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets', github_token=''
        )
        self.project.repo_stats.add(self.repo_stat)

    @staticmethod
    def _tree_url(owner='acme', repo='widgets'):
        return reverse('projects:github-fetch-structure', kwargs={'owner': owner, 'repo': repo})

    @staticmethod
    def _file_url(path, owner='acme', repo='widgets'):
        return reverse('projects:github-fetch-path', kwargs={'owner': owner, 'repo': repo, 'path': path})

    # ---- security ----

    def test_non_member_cannot_browse_a_repo_linked_to_someone_elses_project(self):
        """
        Regression test for a real bug: github_proxy_view/handle_file_content
        took owner/repo straight from the URL with no project reference and
        no membership check at all - any logged-in user could read any
        linked repo's full tree and file contents, private or not, just by
        knowing/guessing the owner/repo pair.
        """
        with patch('projects.views.requests.get') as mock_get:
            self.client.force_login(self.outsider)
            response = self.client.get(self._tree_url(), {'branch': 'main'})
            self.assertEqual(response.status_code, 403)
            mock_get.assert_not_called()

    def test_repo_never_linked_to_any_project_is_inaccessible(self):
        with patch('projects.views.requests.get') as mock_get:
            self.client.force_login(self.owner)
            response = self.client.get(self._tree_url(owner='someoneelse', repo='unlinked-repo'), {'branch': 'main'})
            self.assertEqual(response.status_code, 403)
            mock_get.assert_not_called()

    def test_requires_authentication(self):
        response = self.client.get(self._tree_url(), {'branch': 'main'})
        self.assertEqual(response.status_code, 302)

    def test_non_member_cannot_fetch_file_content_either(self):
        with patch('projects.views.requests.get') as mock_get:
            self.client.force_login(self.outsider)
            response = self.client.get(self._file_url('README.md'), {'branch': 'main'})
            self.assertEqual(response.status_code, 403)
            mock_get.assert_not_called()

    # ---- business logic: tree ----

    def test_project_member_can_fetch_the_tree(self):
        with patch('projects.views.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=200, json=lambda: {'tree': [
                {'path': 'README.md', 'type': 'blob'},
                {'path': 'src', 'type': 'tree'},
            ]})
            self.client.force_login(self.owner)
            response = self.client.get(self._tree_url(), {'branch': 'main'})
            self.assertEqual(response.status_code, 200)
            names = {item['name'] for item in response.json()}
            self.assertEqual(names, {'README.md', 'src'})

    def test_tree_is_cached_after_the_first_fetch(self):
        with patch('projects.views.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=200, json=lambda: {'tree': [{'path': 'README.md', 'type': 'blob'}]})
            self.client.force_login(self.owner)
            self.client.get(self._tree_url(), {'branch': 'main'})
            self.client.get(self._tree_url(), {'branch': 'main'})
            self.assertEqual(mock_get.call_count, 1, "second request should be served from cache, not hit GitHub again")

    def test_falls_back_to_master_when_main_branch_tree_is_missing(self):
        with patch('projects.views.requests.get') as mock_get:
            mock_get.side_effect = [
                Mock(status_code=404, json=lambda: {'message': 'Not Found'}),
                Mock(status_code=200, json=lambda: {'tree': [{'path': 'README.md', 'type': 'blob'}]}),
            ]
            self.client.force_login(self.owner)
            response = self.client.get(self._tree_url(), {'branch': 'main'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_get.call_count, 2)

    def test_github_error_status_is_propagated(self):
        with patch('projects.views.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=500, json=lambda: {'message': 'Server error'})
            self.client.force_login(self.owner)
            response = self.client.get(self._tree_url(), {'branch': 'develop'})
            self.assertEqual(response.status_code, 500)

    # ---- business logic: file content (dispatched for any path with a dot in its last segment) ----

    def test_project_member_can_fetch_file_content(self):
        with patch('projects.views.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=200, json=lambda: {'name': 'README.md', 'content': 'aGVsbG8=', 'encoding': 'base64'})
            self.client.force_login(self.owner)
            response = self.client.get(self._file_url('README.md'), {'branch': 'main'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['name'], 'README.md')

    def test_file_content_is_cached_after_the_first_fetch(self):
        with patch('projects.views.requests.get') as mock_get:
            mock_get.return_value = Mock(status_code=200, json=lambda: {'name': 'README.md', 'content': 'aGVsbG8='})
            self.client.force_login(self.owner)
            self.client.get(self._file_url('README.md'), {'branch': 'main'})
            self.client.get(self._file_url('README.md'), {'branch': 'main'})
            self.assertEqual(mock_get.call_count, 1)


class PushFilesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user('projowner')
        self.member = make_user('projmember')
        self.outsider = make_user('projvisitor')
        self.project = Project.objects.create_project(self.owner.id, f'pushfilesproj_{secrets.token_hex(4)}', 'd')
        developer_role = ProjectRole.objects.get(name='developer')
        UserProjectRole.objects.give_role_to_user(self.project.id, self.member.id, developer_role)
        self.repo_stat = ProjectRepoStats.objects.create(
            github_repo_name='widgets', github_repo_link='https://github.com/acme/widgets', github_token='ghp_token'
        )
        self.project.repo_stats.add(self.repo_stat)

    @staticmethod
    def _url():
        return reverse('projects:push-code')

    def _post(self, payload):
        return self.client.post(self._url(), data=json.dumps(payload), content_type='application/json')

    def _payload(self, **overrides):
        payload = {
            'files': {'README.md': 'new content'},
            'project': self.project.id,
            'repo': 'widgets',
            'owner': 'acme',
            'branch': 'main',
            'message': 'update readme',
        }
        payload.update(overrides)
        return payload

    def _give_member_task_access_to_readme(self):
        task = ProjectTask.objects.add_task_to_project(self.project, 'Task', 'desc', '2025-01-01', '2025-02-01')
        TaskResourceAccess.objects.add_resources_to_task(task, ['README.md'])
        ProjectTaskParticipation.objects.add_task_participations(task, [self.member])

    # ---- business logic ----

    def test_owner_can_push_a_new_file(self):
        with patch('projects.views.requests.get') as mock_get, \
             patch('projects.views.requests.put') as mock_put:
            mock_get.return_value = Mock(status_code=404)  # file doesn't exist yet - no sha to carry
            mock_put.return_value = Mock(status_code=201)

            self.client.force_login(self.owner)
            response = self._post(self._payload())
            self.assertEqual(response.status_code, 200)
            mock_put.assert_called_once()
            self.assertNotIn('sha', mock_put.call_args.kwargs['json'])
            self.assertTrue(AuditLogAction.objects.filter(project=self.project, user=self.owner, action_type='push').exists())

    def test_owner_can_update_an_existing_file(self):
        with patch('projects.views.requests.get') as mock_get, \
             patch('projects.views.requests.put') as mock_put:
            mock_get.return_value = Mock(status_code=200, json=lambda: {'sha': 'existing-sha'})
            mock_put.return_value = Mock(status_code=200)

            self.client.force_login(self.owner)
            response = self._post(self._payload())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_put.call_args.kwargs['json']['sha'], 'existing-sha')

    def test_commit_message_carries_the_hmac_trailer_the_webhook_later_checks_for(self):
        with patch('projects.views.requests.get') as mock_get, \
             patch('projects.views.requests.put') as mock_put:
            mock_get.return_value = Mock(status_code=404)
            mock_put.return_value = Mock(status_code=201)

            self.client.force_login(self.owner)
            self._post(self._payload())

            commit_message = mock_put.call_args.kwargs['json']['message']
            self.assertIn('X-GitSync-Sig:', commit_message)

    def test_non_owner_without_task_access_is_rejected(self):
        with patch('projects.views.requests.put') as mock_put:
            self.client.force_login(self.member)
            response = self._post(self._payload())
            self.assertEqual(response.status_code, 401)
            mock_put.assert_not_called()

    def test_non_owner_with_task_access_and_a_message_can_push(self):
        self._give_member_task_access_to_readme()
        with patch('projects.views.requests.get') as mock_get, \
             patch('projects.views.requests.put') as mock_put:
            mock_get.return_value = Mock(status_code=404)
            mock_put.return_value = Mock(status_code=201)

            self.client.force_login(self.member)
            response = self._post(self._payload())
            self.assertEqual(response.status_code, 200)

    def test_non_owner_missing_commit_message_is_rejected(self):
        self._give_member_task_access_to_readme()
        with patch('projects.views.requests.put') as mock_put:
            self.client.force_login(self.member)
            response = self._post(self._payload(message=''))
            self.assertEqual(response.status_code, 400)
            mock_put.assert_not_called()

    def test_file_locked_by_another_user_is_rejected(self):
        self._give_member_task_access_to_readme()
        ResourceAccess.objects.lock_file('README.md', self.project, self.owner)

        with patch('projects.views.requests.put') as mock_put:
            self.client.force_login(self.member)
            response = self._post(self._payload())
            self.assertEqual(response.status_code, 423)
            mock_put.assert_not_called()

    # ---- security ----

    def test_requires_authentication(self):
        response = self._post(self._payload())
        self.assertEqual(response.status_code, 302)

    # ---- rate limiting ----

    def test_rate_limit_blocks_after_20_requests_per_user(self):
        """push_files rate-limits POST by 'user' at 20/m."""
        with patch('projects.views.requests.get') as mock_get, \
             patch('projects.views.requests.put') as mock_put:
            mock_get.return_value = Mock(status_code=404)
            mock_put.return_value = Mock(status_code=201)

            self.client.force_login(self.owner)
            for attempt in range(20):
                response = self._post(self._payload(files={f'file{attempt}.md': 'x'}))
                self.assertEqual(response.status_code, 200, f"attempt {attempt + 1} should succeed, got {response.status_code}")
            blocked = self._post(self._payload(files={'blocked.md': 'x'}))
            self.assertEqual(blocked.status_code, 403, "21st POST within a minute should be rate-limited (user, 20/m)")