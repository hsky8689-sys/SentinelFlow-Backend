from django.utils import timezone
from collections import defaultdict
from datetime import datetime
import secrets

import django.db
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.db import models, transaction
from django.db.models import QuerySet

from devnetwork.caching import cache_manager, UserCacheKey, ProjectCacheKey
from users.models import User


def generate_app_signing_key():
    return secrets.token_hex(32)


class ProjectManager(models.Manager):
    def makeNewOwner(self, project):
        """

        :param project:
        :return:
        """
        if User.objects.get(project.owner_id) is not None:
            raise ValueError("The owner didnt delete his account")

    def create_project(self, user, name, description):
        """
        Creates a project and automatically sets the given user as owner
        :param user: The future project creator and owner
        :return:
        """
        try:
            validate_slug(name)
        except ValidationError:
            return None
        try:
            with transaction.atomic():
                proj = self.create(owner_id=user, name=name, description=description)
                default_roles = ProjectRole.objects.create_default_project_roles(proj)
                if not UserProjectRole.objects.give_role(proj.owner, proj, default_roles[0][0].id):
                    transaction.set_rollback(True)
                    return None
            cache_manager.delete(UserCacheKey.PROJECTS.format(user_id=user))
            return proj
        except django.db.DatabaseError as e:
            print(str(e))
            return None

    def delete_project(self, project):
        """
        Deletes a project from the database
        :param project:
        :return:
        """
        try:
            deleted_count,_=Project.objects.get(id=project.id).delete()
            return deleted_count > 0
        except django.db.DatabaseError as e:
            print(str(e))
            return False

    def get_user_projects(self, user):
        """
        Returns all the projects that an specified user participated in
        :param project:
        :return:
        """
        cache_key = UserCacheKey.PROJECTS.format(user_id=user.id)
        projects = cache_manager.get(cache_key)
        if projects is None:
            projects = list(self.filter(id__in=UserProjectRole.objects.filter(user_id=user.id).values_list('project_id', flat=True)))
            cache_manager.set(cache_key, projects, timeout=3600)
        return projects


class Project(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=100, blank=False, null=False, default='New project', unique=True, validators=[validate_slug])
    description = models.CharField(max_length=5000, blank=False, null=False, default='Project description')
    can_only_modify_from_app = models.BooleanField(default=False)
    app_signing_key = models.CharField(max_length=64, unique=True, default=generate_app_signing_key)
    repo_stats = models.ManyToManyField('ProjectRepoStats', related_name='projects', blank=True)
    flagged_external_push = models.BooleanField(default=False)
    objects = ProjectManager()

    class Meta:
        db_table = 'projects'


class ProjectRepoStatsManager(models.Manager):
    def get_project_repos(self, project):
        try:
            return self.filter(projects=project)
        except django.db.Error as e:
            print(str(e))
            return []


class ProjectRepoStats(models.Model):
    github_repo_name = models.CharField(max_length=255, blank=False, null=False)
    github_repo_link = models.CharField(max_length=1000, blank=False, null=False)
    github_token = models.CharField(max_length=255, blank=True, default='')
    protected_branch = models.CharField(max_length=255, blank=True, default='')
    previous_branch_protection = models.TextField(blank=True, default='')
    objects = ProjectRepoStatsManager()

    class Meta:
        db_table = 'project_repo_stats'


class ProjectDomainManager(models.Manager):
    def add_domains_to_project(self, project, domain_names):
        """
        :param project:
        :param domain_names:
        :return:
        """
        try:
            with transaction.atomic():
                domains = [ProjectDomain(project=project, domain=name) for name in domain_names]
                succes = self.bulk_create(domains)
            return succes
        except django.db.DatabaseError as e:
            print(str(e))
            return []

    def remove_domains_from_project(self, project, domain_names):
        """

        :param project:
        :param domain_names:
        :return:
        """
        try:
            deleted_count,_ = self.filter(project=project, domain__in=domain_names).delete()
            return deleted_count > 0
        except django.db.DatabaseError as e:
            print(str(e))

    def get_project_domains(self, project):
        return self.filter(project_id=project.id).values('domain')


class ProjectDomain(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    domain = models.CharField(max_length=100, blank=False, null=False, default='new domain')
    objects = ProjectDomainManager()

    class Meta:
        db_table = 'project_domains'


class ProjectTaskManager(models.Manager):
    def get_project_tasks(self, project):
        try:
            return self.select_related('project').filter(project=project)
        except django.db.DatabaseError as e:
            print(str(e))
            return QuerySet()

    def add_task_to_project(self, project, name, description, start_date, end_date):
        try:
            with transaction.atomic():
                _start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
                _end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
                var = self.filter(project=project,name=name)
                if var.count() > 0:
                    return []
                if _start_date > _end_date:
                    return []
                if len(description) > 300:
                    return []
                return self.create(project_id=project.id,
                                   name=name,
                                   description=description,
                                   start_date=start_date,
                                   end_date=end_date,
                                   finished=False
                                   )
        except django.db.DatabaseError as e:
            print(str(e))
            return []

    def remove_tasks_from_project(self,tasks):
        try:
            searched = self.filter(name__in=tasks)
            deleted_count,_=searched.delete()
            return deleted_count > 0
        except django.db.DatabaseError as e:
            print(str(e))
            return 0

class ProjectTask(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=100, default='New task', blank=True)
    description = models.CharField(max_length=300, default='Describe the task..', blank=True)
    start_date = models.DateField(default='1000-10-10')
    end_date = models.DateField(default='3000-10-10')
    finished = models.BooleanField(default=False)
    objects = ProjectTaskManager()

    class Meta:
        db_table = 'projects_tasks'
        ordering = ['end_date']
        indexes = [
            models.Index(fields=['end_date'], name='end_date_idx'),
        ]


class UserRoleValidator():
    def is_operation_permitted(self, project, role_assignator, user, new_role):
        """

        :param role_assignator:
        :param user:
        :param new_role:
        :return:
        """
        _project = Project.objects.get(id=project)
        if _project is None:
            raise ValueError("Project not found")
        _role_assigner = User.objects.get(id=role_assignator)
        if user is None:
            raise ValueError("Role assignator not found")
        _user = User.objects.get(id=user)
        if _user is None:
            raise ValueError("User not found")
        if _project.owner_id == role_assignator:
            return True
        #is_assigner = UserRoleManager.is_user_in_project(_project,_role_assigner)
        #is_user = UserRoleManager.is_user_in_project(_project, _user)
        #if not is_assigner:
        #    raise ValueError("Assigner not found")
        #if not is_user:
        #    raise ValueError("User not found")
        #permission checking TODO
        return True


class ProjectRoleManager(models.Manager):
    def create_default_project_roles(self, project):
        try:
            with transaction.atomic():
                from devnetwork.settings import DEFAULT_PROJECT_ROLES
                created_roles = []
                for role_name, role_permissions in DEFAULT_PROJECT_ROLES.items():
                    role = ProjectRole.objects.get_or_create(
                        name=role_name,
                        defaults=role_permissions
                    )
                    created_roles.append(role)
                return created_roles
        except django.db.Error as e:
            print(str(e))
            return []

    def modify_project_role(self, project, form):
        try:
            print('todo')
        except django.db.Error as e:
            print(str(e))
        except Exception as ex:
            print(str(ex))
    def get_project_roles(self,project):
        try:
            return self.filter(role__project=project).distinct()
        except django.db.Error as e:
            print(str(e))
            return []


class ProjectRole(models.Model):
    name = models.CharField(max_length=50, default='new role', null=False, blank=True)
    can_accept_invites = models.BooleanField(default=False)
    can_invite_others = models.BooleanField(default=False)
    can_kick_others = models.BooleanField(default=False)
    can_change_roles = models.BooleanField(default=False)
    can_create_branches = models.BooleanField(default=False)
    can_merge_branches = models.BooleanField(default=False)
    can_delete_branches = models.BooleanField(default=False)
    can_add_tasks = models.BooleanField(default=False)
    can_delete_tasks = models.BooleanField(default=False)
    can_modify_tasks = models.BooleanField(default=False)
    can_modify_files = models.BooleanField(default=False)
    can_execute_code = models.BooleanField(default=False)
    can_share_file_access = models.BooleanField(default=False)
    can_change_project_settings = models.BooleanField(default=False)
    objects = ProjectRoleManager()

    class Meta:
        db_table = 'project_roles'


class UserProjectRoleManager(models.Manager):
    def make_new_owner(self, project):
        """

        :param project:
        :return:
        """

    def give_role(self, user, project, role):
        try:
            with transaction.atomic():
                role = self.model(user_id=user.id, project_id=project.id, role_id=role)
                role.save()
            cache_manager.delete(ProjectCacheKey.USER_ROLE.format(project_id=project.id, user_id=user.id))
            return True
        except django.db.Error as e:
            print(str(e))
            return False
    def get_user_role_in_project(self, project, user):
        """
        Gets an user's role in a project if it exists,else labels them as visitors
        :param project: a Project instance OR a raw project id (some callers, e.g. push_files, pass the raw id)
        :param user:
        :return:
        """
        project_id = project.id if hasattr(project, 'id') else project
        cache_key = ProjectCacheKey.USER_ROLE.format(project_id=project_id, user_id=user.id)
        cached_role = cache_manager.get(cache_key)
        if cached_role is not None:
            return cached_role
        try:
            role_obj = self.get_queryset().filter(
                project=project,
                user=user
            ).select_related('role').first()
            role_name = role_obj.role.name if role_obj else 'visitor'
        except UserProjectRole.DoesNotExist:
            role_name = 'visitor'
        cache_manager.set(cache_key, role_name, timeout=3600)
        return role_name

    def get_role_permissions(self, role_name, project):
        permission_keys = [
            'can_accept_invites', 'can_invite_others', 'can_kick_others',
            'can_change_roles', 'can_create_branches', 'can_merge_branches',
            'can_delete_branches', 'can_add_tasks',
            'can_delete_tasks', 'can_modify_tasks', 'can_modify_files',
            'can_execute_code', 'can_share_file_access', 'can_change_project_settings'
        ]
        project_id = project.id if hasattr(project, 'id') else project
        cache_key = ProjectCacheKey.ROLE_PERMISSIONS.format(project_id=project_id, role_name=role_name)
        cached_permissions = cache_manager.get(cache_key)
        if cached_permissions is not None:
            return cached_permissions
        try:
            user_project_role = self.get_queryset().filter(
                project=project,
                role__name=role_name,
            ).select_related('role').first()

            if not user_project_role:
                permissions = {k: False for k in permission_keys}
            else:
                role = user_project_role.role
                permissions = {k: getattr(role, k) for k in permission_keys}
        except Exception as e:
            print(str(e))
            permissions = {k: False for k in permission_keys}
        cache_manager.set(cache_key, permissions, timeout=3600)
        return permissions

    def give_role_to_user(self, project: int, user: int, role):
        """

        :param project:
        :param role_assigner:
        :param user:
        :param role:
        :return:
        """
        try:
            with transaction.atomic():
                old_role = self.filter(project_id=project, user_id=user)
                if not old_role.exists():
                    result = self.create(project_id=project, user_id=user, role=role) is not None
                else:
                    old_role.update(role=role)
                    result = True
            cache_manager.delete(ProjectCacheKey.USER_ROLE.format(project_id=project, user_id=user))
            return result
        except (django.db.DatabaseError,ValueError) as e:
            print(str(e))
            return False

    def get_all_users_in_project(self, project):
        """
        Returns the whole users that ever participated/are participating now in a project
        :param project:
        :return: A dictionary with the participants grouped by the roles in the given project
        """
        users_by_role = defaultdict(list)
        roles = self.get_queryset().filter(project=project).select_related('user', 'role')
        for role_obj in roles:
            users_by_role[role_obj.role.name].append(role_obj.user)
        return dict(users_by_role)

    def find_valid_admins(self, project, requested_access):
        """
        Finds the admins that can respond to a file request access in a project
        :param project: the project itself
        :param requested_access: A list of the urls of the requested files for access
        :return: a list of all the admins
        """
        try:
            id_role_owner, id_role_project_manager, id_role_admin = 1, 2, 3
            can_always_respond = [role.user for role in self.filter(
                                        role_id__in=[id_role_owner,id_role_project_manager,id_role_admin],
                                        project_id=project.id
                                        ).select_related('user').distinct()]

            can_also_provide_access = [participation.user for participation in
                                        ProjectTaskParticipation.objects.filter(
                                            task__project_id=project.id,
                                            task__resource_accesses__resource_path__in=requested_access,
                                       ).select_related('user').distinct()
                                      ]
            return can_always_respond + can_also_provide_access
        except django.db.DatabaseError as e:
            print(str(e))
            return None


class UserProjectRole(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, default=-1,related_name='user')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, default=-1,related_name='project')
    role = models.ForeignKey(ProjectRole, on_delete=models.CASCADE, default=-1,related_name='role')
    objects = UserProjectRoleManager()

    class Meta:
        db_table = 'user_project_roles'


class ProjectTaskParticipationManager(models.Manager):
    def add_task_participations(self, task, users):
        try:
            with transaction.atomic():
                participations = [ProjectTaskParticipation(user=user, task=task) for user in users]
                return self.bulk_create(participations)
        except (django.db.DatabaseError,ValueError) as e:
            print(str(e))
            return []

    def remove_task_participations(self, task, users):
        try:
            with transaction.atomic():
                participations = self.filter(task=task, user__in=users)
                participations.delete()
                return True
        except django.db.DatabaseError:
            return False


class ProjectTaskParticipation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    task = models.ForeignKey(ProjectTask, on_delete=models.CASCADE, null=True, blank=True)
    objects = ProjectTaskParticipationManager()

    class Meta:
        db_table = 'project_task_participations'
        managed = False

class ProjectRequiementSectionManager(models.Manager):
    def add_requirement_sections(self, project, names):
        """

        :param project:
        :param names:
        :return:
        """
        try:
            with transaction.atomic():
                new_sections = [ProjectRequirementSection(project=project, name=skill_name) for skill_name in names]
                created = self.bulk_create(new_sections, batch_size=100)
                return created
        except django.db.DatabaseError as e:
            print(str(e))
            return []

    def remove_requirement_sections(self, project, names):
        """

        :param project:
        :param names:
        :return:
        """
        try:
            former_sections = self.filter(project=project, name__in=names)
            deleted_count,_=former_sections.delete()
            return deleted_count
        except django.db.DatabaseError as e:
            print(str(e))
            return 0

    def change_requirement_sections_titles(self, project, old_names, new_names):
        """

        :param project:
        :param old_names:
        :param new_names:
        :return:
        """
        try:
            with transaction.atomic():
                former_sections = self.filter(project=project, name__in=old_names).select_for_update().distinct()
                for index in range(len(former_sections)):
                    former_sections[index].name = new_names[index]
                return former_sections.bulk_update(former_sections,['name'],1000)
        except django.db.DatabaseError as e:
            print(str(e))
            return 0


class ProjectRequirementSection(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=50, null=False, blank=False,
                            default='Choose a new skill section(Frontend/Backend/Database etc..)')
    objects = ProjectRequiementSectionManager()

    class Meta:
        db_table = 'project_requirements_sections'


class ProjectSkillRequirementManager(models.Manager):
    def add_skill_requirements(self, section, names):
        try:
            with transaction.atomic():
                new_requirements = [ProjectSkillRequirement(section=section, name=skill_name) for skill_name in names]
                created = self.bulk_create(new_requirements, batch_size=100)
                return created
        except django.db.DatabaseError as e:
            print(str(e))
            return []

    def remove_skill_requirements(self, section, names):
        """

        :param section:
        :param names:
        :return:
        """
        try:
            reqs = self.filter(section=section, name__in=names)
            former_requirements,_ = reqs.delete()
            return former_requirements > 0
        except django.db.DatabaseError as e:
            print(str(e))
            return False

    def get_requirements_grouped_by_sections(self, project):
        """

        :param project:
        :return:
        """
        try:
            result = {}
            sections = ProjectRequirementSection.objects.filter(project=project)
            requirements = ProjectSkillRequirement.objects.filter(section__in=sections).select_related('section')
            for sec in sections:
                result[sec.name] = []
            for req in requirements:
                result[req.section.name].append({
                    'id': req.id,
                    'skill': req.name
                })
            return result
        except django.db.DatabaseError as e:
            print(str(e))


class ProjectSkillRequirement(models.Model):
    section = models.ForeignKey(ProjectRequirementSection, on_delete=models.CASCADE)
    name = models.CharField(max_length=50, null=False, blank=False,
                            default='Choose a new required skill (Java/Aws/ChatGPT ...)')
    objects = ProjectSkillRequirementManager()

    class Meta:
        db_table = 'project_skill_requirements'

class ResourceAccessManager(models.Manager):
    def is_file_locked(self,file_url,project):
        try:
            entry = self.filter(resource_path=file_url,project=project).select_related('locked_by').first()
            return entry.locked_by if entry else None
        except django.db.DatabaseError as e:
            print(str(e))
            return None

    def lock_file(self, file_url, project, new_writer):
        try:
            with transaction.atomic():
                file_obj = self.filter(resource_path=file_url,project=project).select_for_update()
                new_lock_time = timezone.now() if new_writer else None
                if file_obj.exists():
                    return file_obj.update(locked_by=new_writer,locked_at=new_lock_time) != 0
                else:
                    return self.create(resource_path=file_url,
                                       project=project,
                                       locked_by=new_writer,
                                       locked_at=new_lock_time) is not None
        except (self.model.DoesNotExist,django.db.DatabaseError) as e:
            print(str(e))
            return False

class ResourceAccess(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    resource_path = models.CharField(max_length=255)
    allowed_users = models.ManyToManyField(User, related_name='accessible_resources')
    managers = models.ManyToManyField(User, related_name='managed_resources', blank=True)
    locked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,related_name='locked_resources')
    locked_at = models.DateTimeField(null=True, blank=True)
    objects = ResourceAccessManager()
    class Meta:
        unique_together = ('project', 'resource_path')


class TaskResourceAccessManager(models.Manager):
    def add_resources_to_task(self, task, resource_paths):
        """
        Affiliates the given file/folder paths with a task, granting access
        to every user participating in that task.
        """
        entries = [self.model(task=task, resource_path=path) for path in resource_paths]
        try:
            return self.bulk_create(entries, ignore_conflicts=True)
        except django.db.DatabaseError as e:
            print(str(e))
            return []

    def remove_resources_from_task(self, task, resource_paths):
        try:
            deleted_count,_= self.filter(task=task, resource_path__in=resource_paths).delete()
            return deleted_count > 0
        except django.db.DatabaseError as e:
            print(str(e))
            return False

    def get_user_accessible_paths(self, user, project):
        """
        Resolves every resource_path (trailing '/' stripped) the user can
        touch in this project, in ONE query pair instead of one pair per
        file being checked.
        """
        task_ids = ProjectTaskParticipation.objects.filter(
            user=user, task__project=project
        ).values_list('task_id', flat=True)
        if not task_ids:
            return []
        return [
            path.rstrip('/')
            for path in self.filter(task_id__in=task_ids).values_list('resource_path', flat=True)
        ]

    @staticmethod
    def path_is_covered(file_path, accessible_paths):
        for resource_path in accessible_paths:
            if file_path == resource_path or file_path.startswith(resource_path + '/'):
                return True
        return False

    def user_has_access_to_path(self, user, project, file_path):
        """
        ReBAC check: a user can touch a path if they participate in a task
        that the path (or one of its parent folders) was affiliated with.
        """
        accessible_paths = self.get_user_accessible_paths(user, project)
        return self.path_is_covered(file_path, accessible_paths)


class TaskResourceAccess(models.Model):
    task = models.ForeignKey(ProjectTask, on_delete=models.CASCADE, related_name='resource_accesses')
    resource_path = models.CharField(max_length=255)
    objects = TaskResourceAccessManager()

    class Meta:
        db_table = 'task_resource_accesses'
        unique_together = ('task', 'resource_path')