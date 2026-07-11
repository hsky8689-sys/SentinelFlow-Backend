import json
import django.db
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from projects.github_utils import get_project_tree_paths
from projects.models import Project, UserProjectRole, ProjectDomain, ProjectSkillRequirement, \
    ProjectRequirementSection, ProjectTask, ProjectRole, TaskResourceAccess, ProjectTaskParticipation
from users.models import User

def get_user_file_permissions(user,project):
    try:
        if project is None:
            return {}
        all_project_files = get_project_tree_paths(project,'master')
        role = UserProjectRole.objects.get_user_role_in_project(project, user)
        if role == 'owner':
            return {file: 'ACCESS' for file in all_project_files}
        srv = TaskResourceAccess.objects
        accessible_paths = srv.get_user_accessible_paths(user, project)
        res = {}
        for file in all_project_files:
            if srv.path_is_covered(file, accessible_paths):
                res[file]='ACCESS'
            else:
                res[file]='DENY'
        return res
    except Exception as e:
        print(str(e))
        return {}
def _get_project_domains(request,id):
    try:
        project = get_object_or_404(Project,id=id)
        domains = ProjectDomain.objects.filter(project_id=project.id)
        return JsonResponse({'status':'success','domains':list(domains.values())})
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 500})
def _add_project_domains(request,id):
    try:
        project = get_object_or_404(Project,id=id)
        role = UserProjectRole.objects.get_user_role_in_project(project,request.user)
        if UserProjectRole.objects.get_role_permissions(role,project)['can_change_project_settings']:
            data = json.loads(request.body)
            domains = data.get('newDomains',[])
            succes = ProjectDomain.objects.add_domains_to_project(project,domains)
            return JsonResponse({'status':'succes' if len(succes) == len(domains) else 'error',
                         'code':200 if len(succes) == len(domains) else 404
            })
        else:
            return JsonResponse({'status':'Unauthorized access','code':403})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def _delete_project_domains(request,id):
    try:
            project = get_object_or_404(Project, id=id)
            role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
            if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
                data = json.loads(request.body)
                domains = data.get('removedDomains', [])
                if domains is None or len(domains) == 0:
                    return JsonResponse({'status': 'Bad request by user','message':'No domains were added into request'},status=402)
                success = ProjectDomain.objects.remove_domains_from_project(project, domains)
                if success:
                    return JsonResponse({'status': 'succes','message':'Requested domains were succesfully removed'
                                     },status=200)
                else:
                    return JsonResponse({'status': 'error','message':'Internal server error'
                                         },status=500)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def _get_project_requirements(request,id):
    try:
        project = get_object_or_404(Project,id=id)
        succes = ProjectSkillRequirement.objects.get_requirements_grouped_by_sections(project)
        return JsonResponse({'status':'succes','requirements':succes})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def _add_project_requirements(request,id):
    try:
        with transaction.atomic():
            project = get_object_or_404(Project, id=id)
            role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
            if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
                data = json.loads(request.body)
                requirements = data.get('newRequirements',[])
                if requirements is None or len(requirements) == 0:
                    return JsonResponse({'status': 'Bad request by user', 'message': 'No requirements were added into request'},
                                        status=402)
                manager = ProjectSkillRequirement.objects
                section_manager = ProjectRequirementSection.objects
                batches = {}
                for req in requirements:
                    if batches.get(req[0]):
                        batches[req[0]].append(req[1])
                    else:
                        batches[req[0]] = [req[1]]
                for key in batches.keys():
                    section = section_manager.get(project=project,name=key)
                    added_requirements = manager.add_skill_requirements(section,batches[key])
                    if section is None or (added_requirements is None or len(added_requirements)==0):
                        transaction.set_rollback(True)
                return JsonResponse({'status':'success','message':'Requirements were succesfully added'},status=200)
            else:
                return JsonResponse({'status': 'Unauthorized access'},status=403)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def _remove_project_requirements(request,id):
    try:
        with transaction.atomic():
            project = get_object_or_404(Project, id=id)
            role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
            if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
                data = json.loads(request.body)
                requirements = data.get('removedRequirements',[])
                if requirements is None or len(requirements) == 0:
                    return JsonResponse({'status': 'bad request', 'message':'No requirements added'},status=402)
                manager = ProjectSkillRequirement.objects
                section_manager = ProjectRequirementSection.objects
                batches = {}
                for req in requirements:
                    if batches.get(req[0]):
                        batches[req[0]].append(req[1])
                    else:
                        batches[req[0]] = [req[1]]
                for key in batches.keys():
                    section = section_manager.get(project=project,name=key)
                    removed_requirements = manager.remove_skill_requirements(section,batches[key])
                    if section is None or not removed_requirements:
                        transaction.set_rollback(True)
                return JsonResponse({'status':'success','message':'Requirements were successfully removed'},status=200)
            else:
                return JsonResponse({'status': 'Unauthorized access'},status=403)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def _remove_project_sections(request,id):
    try:
        project = get_object_or_404(Project, id=id)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            requirements = data.get('removedSections',[])
            if requirements is None or requirements == []:
                return JsonResponse({'status': 'error', 'message': 'No sections were requested for deletion'}, status=400)
            deleted = ProjectRequirementSection.objects.remove_requirement_sections(project,requirements)
            if deleted == 0:
                return JsonResponse({'status': 'error', 'message':'Could not delete sections'},status=500)
            else:
                return JsonResponse({'status':'succes','message':'Sections were succesfully deleted'},status=200)
        else:
            return JsonResponse({'status': 'Unauthorized access'},status=403)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': 'Internal server error'},status=500)
def _add_project_sections(request,id):
    try:
        project = get_object_or_404(Project, id=id)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            requirements = data.get('newSections',[])
            if requirements is None or len(requirements) == 0:
                return JsonResponse({'status': 'bad request','message': 'No sections added to the request'}, status=402)
            res = ProjectRequirementSection.objects.add_requirement_sections(project,requirements)
            if res is None or len(res) == 0:
                return JsonResponse({'status': 'error', 'message': 'Sections could not be added'},status=500)
            return JsonResponse({'status':'succes','message':'Sections were successfully added'},status=200)
        else:
            return JsonResponse({'status': 'Unauthorized access'},status=403)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def _get_project_tasks(request,id):
    try:
        project = get_object_or_404(Project, id=id)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            tasks = list(ProjectTask.objects.get_project_tasks(project).values())
            if tasks is None or len(tasks) == 0:
                return JsonResponse({'status': 'success',
                                     'message': 'No tasks were found for the given project',
                                     'tasks': []}, status=404)
            else:
                return JsonResponse({'status': 'success',
                                     'message': 'Tasks were successfully retrieved',
                                     'tasks': tasks}, status=200)
        else:
            return JsonResponse({'status': 'Unauthorized access'},status=403)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'},status=500)
def _add_project_task(request,id):
    try:
        data = json.loads(request.body)
        project = Project.objects.get(id=id)
        if project is None:
            return JsonResponse({'status':'Error','message':'Project does not exist'},status=404)
        title = data.get('title')
        description = data.get('description')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        usernames = data.get('usernames', [])
        resource_paths = data.get('resource_paths', [])

        valid_users = []
        for username in usernames:
            target_user = User.objects.filter(username=username).first()
            if target_user and UserProjectRole.objects.filter(project=project, user=target_user).exists():
                valid_users.append(target_user)

        valid_resource_paths = []
        if resource_paths:
            project_paths = get_project_tree_paths(project)
            valid_resource_paths = [path for path in resource_paths if path in project_paths]

        task = ProjectTask.objects.add_task_to_project(project,title,description,start_date,end_date)
        if not task:
            return JsonResponse({'status':'error','message':'Task could not be created'},status=500)
        if valid_resource_paths:
            TaskResourceAccess.objects.add_resources_to_task(task, valid_resource_paths)
        if valid_users:
            ProjectTaskParticipation.objects.add_task_participations(task, valid_users)

        return JsonResponse({
            'status': 'success',
            'task_id': task.id,
            'resource_paths': valid_resource_paths,
            'affiliated_users': [u.username for u in valid_users]
        }, status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status':'error','message':'Internal server error'},status=500)
def _remove_project_tasks(request,id):
    try:
        project = Project.objects.get(id=id)
        if project is None:
            return JsonResponse({'status':'Error','message':'Project does not exist'},status=404)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            requirements = data.get('removedTasks', [])
            if requirements is None or len(requirements) == 0:
                return JsonResponse({'status': 'bad request',
                                          'message': 'No tasks queued for removal'},
                                          status=402)
            deleted = ProjectTask.objects.remove_tasks_from_project(requirements)
            return JsonResponse({'status':'succes' if deleted else 'error',
                                 'message':'Tasks were successfully removed' if deleted else 'Tasks were not removed'},
                                  status=200 if deleted else 500)
        else:
            return JsonResponse({'status': 'Unauthorized access'},status=403)
    except Exception as e:
        return JsonResponse({'status':'error','message':'Internal server error'},status=500)
def _get_project_roles(request, id):
    try:
        project = Project.objects.get(id=id)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)

        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            project_roles = list(ProjectRole.objects.get_project_roles(project).values())

            all_role_assignments = UserProjectRole.objects.filter(project=project).select_related('user')
            users_by_role_id = {}
            for entry in all_role_assignments:
                users_by_role_id.setdefault(entry.role_id, []).append(entry.user.username)

            for role_dict in project_roles:
                role_dict['users'] = users_by_role_id.get(role_dict['id'], [])

            return JsonResponse({'status': 'success', 'roles': project_roles}, status=200)
        else:
            return JsonResponse({'status': 'Unauthorized access'}, status=403)

    except Project.DoesNotExist:
        return JsonResponse({'status': 'Project not found'}, status=404)
    except Exception as e:
        print(f"Eroare in api_get_project_roles: {str(e)}")
        return JsonResponse({'status': 'error'}, status=500)
def _add_project_role(request, id):
    try:
        project = get_object_or_404(Project, id=id)
        user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(user_role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            can_accept_invites = data.get('can_accept_invites', False)
            can_invite_others = data.get('can_invite_others', False)
            can_kick_others = data.get('can_kick_others', False)
            can_change_roles = data.get('can_change_roles', False)
            can_create_branches = data.get('can_create_branches', False)
            can_merge_branches = data.get('can_merge_branches', False)
            can_delete_branches = data.get('can_delete_branches', False)
            can_add_tasks = data.get('can_add_tasks', False)
            can_delete_tasks = data.get('can_delete_tasks', False)
            can_modify_tasks = data.get('can_modify_tasks', False)
            can_change_project_settings = data.get('can_change_project_settings', False)
            if can_accept_invites and can_invite_others and can_kick_others and can_change_roles and can_create_branches and can_merge_branches and can_delete_branches and can_add_tasks and can_modify_tasks and can_delete_tasks and can_change_project_settings:
                return JsonResponse({'error':'Cannot recreate the owner role'},status=403)
            new_role = ProjectRole.objects.create(
                name=data.get('name'),
                can_accept_invites=can_accept_invites,
                can_invite_others=can_invite_others,
                can_kick_others=can_kick_others,
                can_change_roles=can_change_roles,
                can_create_branches=can_create_branches,
                can_merge_branches=can_merge_branches,
                can_delete_branches=can_delete_branches,
                can_add_tasks=can_add_tasks,
                can_delete_tasks=can_delete_tasks,
                can_modify_tasks=can_modify_tasks,
                can_change_project_settings=can_change_project_settings
            )
            return JsonResponse({'status': 'success', 'role_id': new_role.id}, status=200)
        else:
            return JsonResponse({'status': 'Unauthorized access'}, status=403)
    except Exception as e:
        print(f"Eroare in api_add_project_role: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)