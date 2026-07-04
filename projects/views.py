import ast
import base64
import json
import re
from datetime import datetime

import django.db
import requests
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from django_ratelimit.decorators import ratelimit

from devnetwork import settings
from projects.models import Project, UserProjectRole, ProjectDomain, ProjectSkillRequirement, ProjectRequirementSection, \
    ProjectTask, ProjectRole, ResourceAccess, TaskResourceAccess, ProjectTaskParticipation
from users.models import User, UserRequest


def get_user_file_permissions(user,project):
    try:
        if project is None:
            return {}
        all_project_files = get_project_tree_paths(project,'master')
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
@login_required
@csrf_protect
@require_GET
@ratelimit(key='user',rate='120/m',block=True)
def open_project_page(request,name):
    project = Project.objects.filter(name=name).first()
    if not project:
        return JsonResponse({'status': 'failed', 'code': 404})
    staff = UserProjectRole.objects.get_all_users_in_project(project)
    user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
    visitor_permissions = UserProjectRole.objects.get_role_permissions(user_role,project)
    project_domains = ProjectDomain.objects.get_project_domains(project)
    owner_username,repo_name='no_github_owner_set','no_github_name_set'
    branches = []
    if project.root_link:
        root_link = project.root_link.split('/')
        owner_username,repo_name = root_link[3],root_link[4]
        if owner_username is not None and repo_name is not None:
            branches= get_all_github_repo_branches(owner_username,repo_name)
    file_permissions = get_user_file_permissions(request.user,project)
    context_data = {
        'role': user_role,
        'user_id': request.user.id,
        'user_username': request.user.username,
        'project_name': project.name,
        'project_id': project.id,
        'owner_github_name':owner_username,
        'repo_name':repo_name,
        'repository_link' : project.root_link,
        'staff': staff,
        'branches':branches,
        'roles': list(staff.keys()),
        'domains':list(project_domains),
        'description':project.description,
        'visitor_permissions':visitor_permissions,
        'files_permissions': file_permissions
    }
    return render(request, 'html/project_page.html', {'stats': context_data})
@login_required
@csrf_protect
@require_GET
@ratelimit(key='user',rate='120/m',block=True)
def open_project_members_page(request,name):
    project = Project.objects.filter(name=name).first()
    result = UserProjectRole.objects.get_all_users_in_project(project)
    stats = {'members': result, 'project_name': project.name}
    return render(request, 'html/project_members_page.html', {'stats': stats})

@login_required
@csrf_protect
@require_GET
@ratelimit(key='user',rate='60/m',block=True)
def open_project_settings(request, name):
    project = get_object_or_404(Project, name=name)
    user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
    permissions = UserProjectRole.objects.get_role_permissions(user_role, project)

    if not permissions['can_change_project_settings']:
        return JsonResponse({'error': 'Unauthorized access', 'code': 403})

    context_data = {
        'project_name': project.name,
        'project_id': project.id,
        'role': user_role,
        'user_username': request.user.username,
    }
    return render(request, 'html/project_settings_page.html', {'stats': context_data})
@login_required
@csrf_protect
@require_GET
@ratelimit(key='user',rate='120/m',block=True)
def api_get_project_domains(request,name):
    try:
        project = get_object_or_404(Project,name=name)
        domains = ProjectDomain.objects.filter(project_id=project.id)
        return JsonResponse({'status':'success','domains':list(domains.values())})
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 500})
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='30/m',block=True)
def api_add_project_domains(request,name):
    try:
        if request.method == 'POST':
            project = get_object_or_404(Project,name=name)
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
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='30/m',block=True)
def api_delete_project_domains(request,name):
    try:
            project = get_object_or_404(Project, name=name)
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
@login_required
@csrf_protect
@require_GET
@ratelimit(key='user',rate='120/m',block=True)
def api_get_project_requirements(request,name):
    try:
        project = get_object_or_404(Project,name=name)
        succes = ProjectSkillRequirement.objects.get_requirements_grouped_by_sections(project)
        return JsonResponse({'status':'succes','requirements':succes})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='30/m',block=True)
def api_add_project_requirements(request,name):
    try:
        with transaction.atomic():
            project = get_object_or_404(Project, name=name)
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
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='30/m',block=True)
def api_remove_project_requirements(request,name):
    try:
        with transaction.atomic():
            project = get_object_or_404(Project, name=name)
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
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='30/m',block=True)
def api_remove_project_sections(request,name):
    try:
        project = get_object_or_404(Project, name=name)
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
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='30/m',block=True)
def api_add_project_sections(request,name):
    try:
        project = get_object_or_404(Project, name=name)
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
@login_required
@csrf_protect
@require_GET
@ratelimit(key='user',rate='120/m',block=True)
def api_get_project_tasks(request,name):
    try:
        project = get_object_or_404(Project, name=name)
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
def get_project_owner_repo(project):
    root_link_parts = project.root_link.split('/')
    if len(root_link_parts) < 5:
        return None, None
    return root_link_parts[3], root_link_parts[4]

def fetch_github_tree_with_sizes(owner, repo, branch='main'):
    """
    Fetches the recursive git tree from GitHub, keyed by path, including each
    blob's size so callers can detect when a cached tree has gone stale.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if hasattr(settings, 'GITHUB_TOKEN'):
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"

    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    response = requests.get(url, headers=headers)

    if response.status_code == 404 and branch == 'main':
        branch = 'master'
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return {}, branch

    raw_tree = response.json().get('tree', [])
    tree_by_path = {
        item['path']: {
            'path': item['path'],
            'type': 'dir' if item['type'] == 'tree' else 'file',
            'size': item.get('size', 0),
        }
        for item in raw_tree
    }
    return tree_by_path, branch


def get_project_tree_paths(project, branch='main'):
    """
    Returns the set of every file/folder path that exists in the project's
    github repo, reading from the same redis cache used by github_proxy_view.
    If the tree isn't cached yet, it's fetched from the GitHub API and cached.
    """
    root_link_parts = project.root_link.split('/')
    if len(root_link_parts) < 5:
        return set()
    owner, repo = root_link_parts[3], root_link_parts[4]

    cache_key = f"github_tree_recursive_{owner}_{repo}_{branch}"
    tree = cache.get(cache_key)
    if tree:
        return {item['path'] for item in tree}

    headers = {"Accept": "application/vnd.github.v3+json"}
    if hasattr(settings, 'GITHUB_TOKEN'):
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"

    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    response = requests.get(url, headers=headers)

    if response.status_code == 404 and branch == 'main':
        branch = 'master'
        cache_key = f"github_tree_recursive_{owner}_{repo}_{branch}"
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return set()

    raw_tree = response.json().get('tree', [])
    formatted_tree = [{
        'name': item['path'].split('/')[-1],
        'path': item['path'],
        'type': 'dir' if item['type'] == 'tree' else 'file'
    } for item in raw_tree]
    cache.set(cache_key, formatted_tree, timeout=3600)
    return {item['path'] for item in formatted_tree}

@login_required
@csrf_exempt
@require_POST
@ratelimit(key='user', rate='30/m', block=True)
def api_add_project_task(request,name):
    try:
        data = json.loads(request.body)
        project = Project.objects.get(name=name)
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
@login_required
@csrf_protect
@require_http_methods(["DELETE"])
@ratelimit(key='user', rate='30/m', block=True)
def api_remove_project_tasks(request,name):
    try:
        project = Project.objects.get(name=name)
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

@login_required
@csrf_protect
@require_GET
@ratelimit(key='user', rate='120/m', block=True)
def api_get_project_roles(request, name):
    try:
        project = Project.objects.get(name=name)
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

@login_required
def filter_tree_by_path(request,tree, current_path):
    result = []
    for item in tree:
        item_path = item['path']

        if current_path == "":
            if '/' not in item_path:
                result.append(item)
        else:
            if item_path.startswith(current_path + '/'):
                sub_path = item_path[len(current_path) + 1:]
                if '/' not in sub_path:
                    result.append(item)
    return result

@login_required
@csrf_exempt
def handle_file_content(request,owner, repo, path, branch='main'):
    cache_key = f"github_file_{owner}_{repo}_{branch}_{path.replace('/', '_')}"
    cached_file = cache.get(cache_key)
    if cached_file:
        return JsonResponse(cached_file, safe=False)

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if hasattr(settings, 'GITHUB_TOKEN'):
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"

    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        cache.set(cache_key, r.json(), timeout=3600)
        return JsonResponse(r.json(), safe=False)
    return JsonResponse(r.json(), status=r.status_code, safe=False)
def invalidate_repo_cache(repo:str,owner:str):
    """
    Invalidates every cached entry for a project's repo: the recursive tree
    listings (for both 'main' and 'master') and every per-file/sub-folder
    content cache, so a push is immediately reflected instead of serving
    stale cached structure/content on the next request.
    """
    try:
        for branch in ('main', 'master'):
            cache.delete(f"github_tree_recursive_{owner}_{repo}_{branch}")
            cache.delete(f"github_tree_with_size_{owner}_{repo}_{branch}")

        stale_keys = list(cache.keys(f"github_file_{owner}_{repo}_*"))
        stale_keys += list(cache.keys(f"file_content_{owner}_{repo}_*"))
        if stale_keys:
            cache.delete_many(stale_keys)
    except Exception as e:
        print(str(e))
@login_required
def github_proxy_view(request, owner, repo, path=""):
    #invalidate_repo_cache(repo,owner)
    branch = request.GET.get('branch') or get_default_branch(owner, repo) or "main"
    if path != "" and '.' in path.split('/')[-1]:
        return handle_file_content(request,owner, repo, path, branch)

    cache_key = f"github_tree_recursive_{owner}_{repo}_{branch}"

    cached_tree = cache.get(cache_key)
    if cached_tree:
        return JsonResponse(filter_tree_by_path(request,cached_tree, path), safe=False)

    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if hasattr(settings, 'GITHUB_TOKEN'):
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"

    response = requests.get(url, headers=headers)

    if response.status_code == 404 and branch == "main":
        branch = "master"
        cache_key = f"github_tree_recursive_{owner}_{repo}_{branch}"
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/master?recursive=1"
        response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return JsonResponse({'error': 'Nu am putut lua arborele'}, status=response.status_code)

    raw_tree = response.json().get('tree', [])

    formatted_tree = []
    for item in raw_tree:
        formatted_tree.append({
            'name': item['path'].split('/')[-1],
            'path': item['path'],
            'type': 'dir' if item['type'] == 'tree' else 'file'
        })
    cache.set(cache_key, formatted_tree, timeout=3600)
    return JsonResponse(filter_tree_by_path(request,formatted_tree, path), safe=False)
@login_required
@csrf_exempt
@require_POST
@ratelimit(key='user', rate='20/m',block=True)
def proxy_run_code(request):
    try:
        body = json.loads(request.body)
        source_code = body.get("source_code","")
        language_id = body.get("language_id",71)
        project_name = body.get("project")
        if not source_code or source_code == "":
            return JsonResponse({'error': 'Missing source code'}, status=400)
        if not project_name:
            return JsonResponse({'error':'Missing project'},status=400)
        project = Project.objects.filter(name=project_name).first()
        if not project:
            return JsonResponse({'error':'Project does not exist'},status=404)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if role == 'visitor':
            return JsonResponse({'error':'Visitor cannot execute code in this project'},status=403)
        if not UserProjectRole.objects.get_role_permissions(role, project)['can_execute_code']:
            return JsonResponse({'error':'You do not have the permission to execute code in this project'},status=403)

        if not source_code:
            return JsonResponse({'error':'Code fragment is empty'},status=400)
        url = settings.RAPIDAPI_URL
        headers = {
            'Content-Type':'application/json',
            'X-RapidAPI-Key':settings.RAPIDAPI_KEY,
            'X-RapidAPI-Host':settings.RAPIDAPI_HOST
        }
        payload = {
            'source_code':source_code,
            'language_id':language_id,
            'stdin':""
        }
        response = requests.post(url,json=payload,headers=headers)
        return JsonResponse(response.json(),status=response.status_code,safe=False)
    except Exception as e:
        return JsonResponse({'error':'Internal server error','message':str(e)},status=500)
@login_required
@csrf_exempt
@require_POST
@ratelimit(key='user', rate='30/m', block=True)
def request_file_open(request):
    try:
        user = request.user
        if user is None or not user.is_authenticated:
            return JsonResponse({'error': 'User is required'}, status=401)

        data = json.loads(request.body)
        project_id = data.get('project_id')
        if not project_id:
            return JsonResponse({'error': 'project_id is required'}, status=400)

        project = Project.objects.filter(id=project_id).first()
        if project is None:
            return JsonResponse({'error': 'Project does not exist'}, status=404)

        files = data.get('file_urls', [])
        if not files:
            return JsonResponse({'error': 'No files were requested'}, status=400)

        role = UserProjectRole.objects.get_user_role_in_project(project,user)
        if role == 'visitor':
            return JsonResponse({'error': 'User is not part of the project'}, status=403)
        if not UserProjectRole.objects.get_role_permissions(role, project)['can_execute_code']:
            return JsonResponse({'error': 'User is part of the project but cannot run code'}, status=403)

        def find_files_from_project(project, requested_files):
            """
            Splits requested_files into 3 lists, reading the project's github
            tree from cache first and only hitting the GitHub API for paths
            that are missing. If the GitHub blob sizes differ from what's
            cached, the cache is refreshed and the lookup is retried.

            Returns (requested_access, not_in_project, already_has_access):
              - requested_access: paths that exist in the project but `user`
                doesn't have access to yet (an access request should be sent)
              - not_in_project: paths that aren't part of the project's repo
              - already_has_access: paths that exist and `user` already has
                access to
            """
            owner, repo = get_project_owner_repo(project)
            if not owner or not repo:
                return [], list(requested_files), []

            branch = 'main'
            cache_key = f"github_tree_with_size_{owner}_{repo}_{branch}"
            tree_by_path = cache.get(cache_key)

            def split_by_presence(paths, tree):
                present, missing = {}, []
                for path in paths:
                    if tree and path in tree:
                        present[path] = tree[path]
                    else:
                        missing.append(path)
                return present, missing

            present, missing = split_by_presence(requested_files, tree_by_path)

            if missing:
                fresh_tree, resolved_branch = fetch_github_tree_with_sizes(owner, repo, branch)
                stale = not tree_by_path or any(
                    tree_by_path.get(path, {}).get('size') != item['size']
                    for path, item in fresh_tree.items()
                )
                if stale:
                    fresh_cache_key = f"github_tree_with_size_{owner}_{repo}_{resolved_branch}"
                    cache.set(fresh_cache_key, fresh_tree, timeout=3600)
                tree_by_path = fresh_tree
                present, missing = split_by_presence(requested_files, tree_by_path)

            requested_access, already_has_access = [], []
            for path in present.keys():
                resource_access = ResourceAccess.objects.filter(project=project, resource_path=path).first()
                if resource_access and user in resource_access.allowed_users.all():
                    already_has_access.append(path)
                else:
                    requested_access.append(path)

            return requested_access, missing, already_has_access

        requested_access, not_in_project, already_has_access = find_files_from_project(project, files)

        if not requested_access and not already_has_access:
            return JsonResponse({'error':'No requested files are part of this project'},status=404)

        admins = UserProjectRole.objects.find_valid_admins(project,requested_access)
        if admins is None or len(admins) == 0:
            return JsonResponse({'error':'No admins can respond to this request'},status=401)

        if requested_access and not UserRequest.objects.send_files_access_request(user,project,requested_access,admins):
            return JsonResponse({'error':'Internal server error'},status=500)

        response_payload = {
            'succes': 'A request for the files from this project has been sent',
            'requested_access': requested_access,
            'already_has_access': already_has_access,
            'not_in_project': not_in_project,
        }
        if not_in_project:
            response_payload['message'] = 'User requested permission for some files not found in this project'
            return JsonResponse(response_payload, status=206)

        return JsonResponse(response_payload, status=200)
    except Exception as e:
        return JsonResponse({'error':str(e)},status=500)
@login_required
@csrf_exempt
@require_GET
@ratelimit(key='user',rate='60/m',block=True)
def api_get_availible_languages(request):
    cache_key = "cache_key_availible_languages"
    if request.GET.get('invalidate') == 'true':
        cache.delete(cache_key)
    cached_languages = cache.get(cache_key)
    if cached_languages:
        return JsonResponse({'status': 'success', 'languages': cached_languages, 'source': 'cache'}, status=200)
    try:
        url = f"https://{settings.RAPIDAPI_HOST}/languages"
        headers = {
            'X-RapidAPI-Key': settings.RAPIDAPI_KEY,
            'X-RapidAPI-Host': settings.RAPIDAPI_HOST
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            languages = response.json()
            cache.set(cache_key, languages, timeout=604800)
            return JsonResponse({'status': 'success', 'languages': languages, 'source': 'api'}, status=200)
    except Exception as e:
        return JsonResponse({'status':'error','message':str(e)},status=500)
def is_repo_private(owner,repo):
    try:
        token = settings.GITHUB_TOKEN
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json"
        }
        url = f"https://api.github.com/repos/{owner}/{repo}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data['visibility'] == 'private'
    except Exception as e:
        print(str(e))
def get_default_branch(owner,repo):
    try:
        headers = {
            "Authorization": f"token {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        url = f"https://api.github.com/repos/{owner}/{repo}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get('default_branch')
        return None
    except Exception as e:
        print(str(e))
        return None
def get_branch_sha(owner,repo,branch_name):
    try:
        headers = {
            "Authorization": f"token {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch_name}"
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()['object']['sha']
        return None
    except Exception as e:
        print(str(e))
        return None
def get_all_github_repo_branches(owner,repo):
    try:
        with transaction.atomic():
            url = f'https://api.github.com/repos/{owner}/{repo}/branches'
            headers = {"Authorization": f"token {settings.GITHUB_TOKEN}"}
            meta_res = requests.get(url, headers=headers) if is_repo_private(owner,repo) else requests.get(url)
            if meta_res.ok:
                branches_json = meta_res.json()
                branches = [br['name'] for br in branches_json]
                return branches
            else:
                return []
    except Exception as e:
        print(str(e))
        return []
@login_required
@csrf_exempt
@require_GET
@ratelimit(key='user', rate='60/m', block=True)
def api_github_get_all_repo_branches(request):
    try:
        data = request.GET
        repo_url = data.get('repo')
        project_name = data.get('project')
        if not all([repo_url,project_name]):
            return JsonResponse({'status':'bad request',
                                      'message':'wrong repo url or project name provided'},
                                       status=403)
        project = get_object_or_404(Project, name=project_name)
        owner,repo = get_project_owner_repo(project)
        if not all([owner,repo]):
            return JsonResponse({'status':'bad request',
                                      'message':'wrong url privided'},
                                       status=403)
        received = get_all_github_repo_branches(owner,repo)
        response_good = received is not None and len(received) > 0
        return JsonResponse({'status': 'success' if response_good else 'error',
                                 'message': 'Branches fully received' if response_good else 'Could not receive branches',
                                 'branches': received},
                                status=200 if response_good else 500)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
@login_required
@csrf_exempt
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def push_files(request):
    try:
        data = json.loads(request.body)
        files = data.get('files',{})
        project = data.get('project')
        repo = data.get('repo')
        owner = data.get('owner')
        branch = data.get('branch')
        default_msg = data.get('message','')
        role = UserProjectRole.objects.get_user_role_in_project(project,request.user)
        if role != 'owner':
            if not all(TaskResourceAccess.objects.user_has_access_to_path(request.user,project,path) for path in files):
                return JsonResponse({'error': 'cannot push certain chosen files'}, status=401)
            if default_msg is None or default_msg == '':
                return JsonResponse({'error':'cannot push with no message'},status=400)

            locked_by_others = {}
            for path in files:
                holder = ResourceAccess.objects.is_file_locked(path, project)
                if holder is not None and holder.id != request.user.id:
                    locked_by_others[path] = holder.username
            if locked_by_others:
                return JsonResponse({
                    'error': 'some files are locked by another user',
                    'locked_files': locked_by_others
                }, status=423)
        message = f'[Pushed via GitSync]:{default_msg}'

        headers = {
            "Authorization": f"token {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }

        errors = []
        for path, content in files.items():
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
            sha = None

            meta_res = requests.get(f"{url}", headers=headers)
            if meta_res.status_code == 200:
                sha = meta_res.json().get('sha')

            encoded_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')

            put_url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
            payload = {
                "message": message,
                "content": encoded_content,
                "branch": branch
            }
            if sha:
                payload["sha"] = sha

            put_res = requests.put(put_url, json=payload, headers=headers)

            if put_res.status_code in [200, 201]:
                cache_key = f"file_content_{owner}_{repo}_{branch}_{path}"
                cache.set(cache_key, content, timeout=3600)
            else:
                errors.append({'path':path,'error': put_res.json()})

        invalidate_repo_cache(repo, owner)
        if errors:
            return JsonResponse({'status': 'partial_error', 'errors': errors}, status=400)
        return JsonResponse({'status': 'success'})
    except Exception as e:
        print(str(e))
        return JsonResponse({'error':str(e)},status=500)

@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_add_project_role(request, project_id):
    try:
        project = get_object_or_404(Project, id=project_id)
        user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(user_role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            can_accept_invites = data.get('can_accept_invites', False)
            can_invite_others = data.get('can_invite_others', False)
            can_kick_others = data.get('can_kick_others', False)
            can_change_roles = data.get('can_change_roles', False)
            can_start_calls = data.get('can_start_calls', False)
            can_add_tasks = data.get('can_add_tasks', False)
            can_delete_tasks = data.get('can_delete_tasks', False)
            can_modify_tasks = data.get('can_modify_tasks', False)
            can_change_project_settings = data.get('can_change_project_settings', False)
            if can_accept_invites and can_invite_others and can_kick_others and can_change_roles and can_start_calls and can_add_tasks and can_modify_tasks and can_delete_tasks and can_change_project_settings:
                return JsonResponse({'error':'Cannot recreate the owner role'},status=403)
            new_role = ProjectRole.objects.create(
                project=project,
                name=data.get('name'),
                can_accept_invites=can_accept_invites,
                can_invite_others=can_invite_others,
                can_kick_others=can_kick_others,
                can_change_roles=can_change_roles,
                can_start_calls=can_start_calls,
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

@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_assign_users_to_role(request, id):
    try:
        project = get_object_or_404(Project,id=id)
        user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)

        if UserProjectRole.objects.get_role_permissions(user_role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            role_id = data.get('role_id')
            usernames = data.get('usernames', [])

            target_role = get_object_or_404(ProjectRole, id=role_id, project=project)

            assigned_users = []

            for username in usernames:
                try:
                    target_user = User.objects.get(username=username)

                    with transaction.atomic():
                        UserProjectRole.objects.filter(project=project, user=target_user).delete()
                        UserProjectRole.objects.create(project=project, user=target_user, role=target_role)
                    assigned_users.append(username)

                except User.DoesNotExist:
                    print(f"Userul {username} nu exista in baza de date, il sarim.")
                    continue

            return JsonResponse({'status': 'success', 'assigned': assigned_users}, status=200)
        else:
            return JsonResponse({'status': 'Unauthorized access'}, status=403)

    except Exception as e:
        print(f"Eroare in api_assign_users_to_role: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_share_file_access(request, name):
    try:
        project = get_object_or_404(Project, name=name)
        project_owner = project.owner
        user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)

        data = json.loads(request.body)
        file_path = data.get('file_path')
        target_usernames = data.get('usernames', [])
        give_management_rights = data.get('make_manager', False)

        can_modify_files = UserProjectRole.objects.get_role_permissions(user_role, project)['can_modify_files']

        resource_access = ResourceAccess.objects.filter(project=project, resource_path=file_path).first()
        is_file_manager = resource_access and request.user in resource_access.managers.all()

        if not (can_modify_files or is_file_manager):
            return JsonResponse({'status': 'Unauthorized', 'message': 'You do not have the right to share this file'},status=403)
        if not resource_access:
            resource_access = ResourceAccess.objects.create(project=project, resource_path=file_path)
            resource_access.managers.add(request.user)
            resource_access.managers.add(project_owner)
            resource_access.allowed_users.add(project_owner)
            resource_access.allowed_users.add(request.user)
        success_shared = []
        for username in target_usernames:
            try:
                user_to_add = User.objects.get(username=username)
                resource_access.allowed_users.add(user_to_add)
                if give_management_rights:
                    resource_access.managers.add(user_to_add)
                success_shared.append(username)
            except User.DoesNotExist:
                continue
        return JsonResponse({'status': 'success', 'shared_with': success_shared}, status=200)
    except Exception as e:
        print(f"Eroare in api_share_file_access: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_request_project_join(request, project_id):
    try:
        project = get_object_or_404(Project, id=project_id)

        if UserProjectRole.objects.get_user_role_in_project(project, request.user) != 'visitor':
            return JsonResponse({'status': 'error', 'message': 'Already member of this project.'}, status=400)

        pending_exists = UserRequest.objects.filter(
            sender=request.user,
            request_type='project',
            target=str(project.id),
            status='pending'
        ).exists()

        if pending_exists:
            return JsonResponse({'status': 'error', 'message': 'Already requested to join this project.'}, status=400)

        project_admins = User.objects.filter(
            user__project=project,
            user__role__can_change_project_settings=True
        ).distinct()

        if not project_admins.exists():
            return JsonResponse({'status': 'error', 'message': 'Project has no registered admins'}, status=500)

        with transaction.atomic():
            for admin in project_admins:
                UserRequest.objects.update_or_create(
                    sender=request.user,
                    receiver=admin,
                    defaults={
                        'request_type': 'project',
                        'target': str(project.id),
                        'status': 'pending'
                    }
                )

        return JsonResponse({'status': 'success', 'message': 'Request successfully sent!'}, status=200)

    except Project.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Project does not exist.'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_handle_project_join_request(request):
    try:
        data = json.loads(request.body)
        action = data.get('action')
        sender_id = data.get('sender_id')
        receiver_id = data.get('receiver_id')

        if not all([action, sender_id, receiver_id]):
            return JsonResponse({'status': 'error', 'message': 'Missing parameters in request.'}, status=400)

        user_req = get_object_or_404(
            UserRequest,
            sender_id=sender_id,
            receiver_id=receiver_id,
            request_type='project',
            status='pending'
        )

        project = get_object_or_404(Project, id=int(user_req.target))

        if UserProjectRole.objects.get_user_role_in_project(project, user_req.sender) != 'visitor':
            user_req.status = 'accepted'
            user_req.save()
            return JsonResponse({'status': 'error', 'message': 'User is already a member of this project.'}, status=400)

        if action == 'accept':
            with transaction.atomic():
                UserProjectRole.objects.create(
                    user=user_req.sender,
                    project=project,
                    role=ProjectRole.objects.get(name='newbie') # Rolul tău default
                )
                user_req.status = 'accepted'
                user_req.save()
            return JsonResponse({'status': 'success', 'message': 'User successfully added to the project!'}, status=200)

        elif action in ['reject', 'deny', 'declined']:

            user_req.status = 'declined'
            user_req.save()
            return JsonResponse({'status': 'success', 'message': 'Project join request declined.'}, status=200)

        else:
            return JsonResponse({'status': 'error', 'message': 'Unknown action.'}, status=400)

    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON format.'}, status=400)
    except ProjectRole.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Default role "newbie" was not found in DB.'}, status=500)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_handle_file_access_request(request):
    try:
        data = json.loads(request.body)
        response = data.get('response')
        sender_id = data.get('sender_id')
        receiver_id = data.get('receiver_id')

        if not all([response, sender_id, receiver_id]):
            return JsonResponse({'status': 'error', 'message': 'Missing parameters in request.'}, status=400)

        user_req = get_object_or_404(
            UserRequest,
            sender_id=sender_id,
            receiver_id=receiver_id,
            request_type='file_access',
            status='pending'
        )

        is_accepted = str(response).lower() in ('accept', 'accepted', 'true', '1', 'yes')

        if not is_accepted:
            user_req.status = 'declined'
            user_req.save()
            return JsonResponse({'status': 'success', 'message': 'File access request declined.'}, status=200)

        match = re.search(r"files (\[.*\]) in project (.+)$", user_req.target or '')
        if not match:
            user_req.status = 'declined'
            user_req.save()
            return JsonResponse({'status': 'error', 'message': 'Could not parse the requested files for this request.'}, status=400)

        try:
            requested_files = ast.literal_eval(match.group(1))
        except (ValueError, SyntaxError):
            requested_files = []
        project_name = match.group(2)

        project = Project.objects.filter(name=project_name).first()
        if not project:
            user_req.status = 'declined'
            user_req.save()
            return JsonResponse({'status': 'error', 'message': 'Project for this request no longer exists.'}, status=400)

        # TODO: let the responder pick the task; for now we attach the access
        # to the most recent task the requesting user is already affiliated with.
        latest_task_id = ProjectTaskParticipation.objects.filter(
            user_id=sender_id,
            task__project=project
        ).aggregate(Max('task_id'))['task_id__max']

        if not latest_task_id:
            user_req.status = 'declined'
            user_req.save()
            return JsonResponse({'status': 'error', 'message': 'User is not affiliated with any task in this project.'}, status=400)

        task = ProjectTask.objects.get(id=latest_task_id)
        with transaction.atomic():
            if requested_files:
                added = TaskResourceAccess.objects.add_resources_to_task(task, requested_files)
                if not added:
                    return JsonResponse({'status': 'error', 'message': 'Could not grant access to the requested files.'}, status=500)

            user_req.status = 'accepted'
            user_req.save()

        return JsonResponse({
            'status': 'success',
            'message': 'File access request accepted.',
            'task_id': latest_task_id,
            'files': requested_files
        }, status=200)
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON format.'}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='60/m',block=True)
def api_request_file_share(request):
    try:
        data = json.loads(request.body)
        sender = request.user
        if not sender.is_authenticated:
            return JsonResponse({
                                      'status': 'error',
                                      'message': 'User is not authenticated'
                                      },status='400')
        project_name=data.get('project','')
        file_url=data.get('file_url','')
        if file_url == '':
            return JsonResponse({
                'status': 'Bad request',
                'message': 'User did not add a file to the request'
            }, status='402')
        project = get_object_or_404(Project,name=project_name)

        project_files = get_project_tree_paths(project,'main')
        # de facut branch-uri si cacheuit cumva asta.....
        if not file_url in project_files:
            return JsonResponse({
                'status': 'Bad request',
                'message': 'User requested permission to a file from another project'
            }, status='402')

        user_role = UserProjectRole.objects.get_user_role_in_project(project, sender)
        if user_role != 'owner':
            permissions = UserProjectRole.objects.get_role_permissions(user_role, project)
            if not permissions['can_modify_files']:
                return JsonResponse({'status': 'unauthorized access'}, status='403')

            task_access = TaskResourceAccess.objects
            if not task_access.user_has_access_to_path(sender, project,file_url):
                return JsonResponse({'status': 'unauthorized access',
                                          'message':'User does not have access to file'}, status='403')

        resource_access = ResourceAccess.objects
        current_writer = resource_access.is_file_locked(file_url,project)
        if current_writer is None or current_writer.id == sender.id:
            res = resource_access.lock_file(file_url,project,sender)
            return JsonResponse({'status':'success' if res else 'error',
                                      'message':'File successfully locked' if res else 'Could not lock file'
                                      },status=200 if res else 500)
        else:
            res = UserRequest.objects.send_file_move_access_request(file_url,sender,current_writer,project)
            return JsonResponse({'status': 'success' if res else 'error',
                                 'message': 'Request was successfully sent' if res else 'Could not send request'
                                 }, status=200 if res else 500)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'},status=500)
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_handle_request_file_share(request):
    if not request.user.is_authenticated:
        return JsonResponse({
            'status': 'error',
            'message': 'User is not authenticated'
        }, status='400')
    try:
        data = json.loads(request.body)
        response = data.get('response')
        sender_id = data.get('sender_id')
        receiver_id = data.get('receiver_id')

        checked = UserRequest.objects.filter(
            sender_id=sender_id,receiver_id=receiver_id,request_type='move_file_access'
        ).order_by('-timestamp').first()

        if checked is None:
            return JsonResponse({
                'status': 'Not found',
                'message': 'File share requests with sender_id:{} and receiver_id:{} was not found'.format(sender_id,receiver_id)
            }, status='404')

        receiver_id = checked.receiver_id
        if receiver_id != request.user.id:
            return JsonResponse({
                'status': 'Bad request',
                'message': 'File share requests may only be handled by the receiver'
            }, status='403')#pe viitor o sa modific ca owneru sa aiba drept suprem...

        if checked.request_type != 'move_file_access':
            return JsonResponse({
                'status': 'Bad request',
                'message': 'File share requests with sender_id:{} and receiver_id:{} has wrong type for this request:{}'.format(sender_id,receiver_id,checked.request_type)
            }, status='402')
        if checked.status != 'pending':
            return JsonResponse({
                'status': 'Bad request',
                'message': 'File share requests with sender_id:{} and receiver_id:{} has already been {}'.format(
                    sender_id,receiver_id,checked.status)
            }, status='402')

        if response == 'ACCEPT':
            res = UserRequest.objects.handle_move_file_access_request(sender_id,receiver_id,response)
            status='success' if res else 'error'
            message='Request with with sender_id:{} and receiver_id:{} has been successfully accepted'.format(sender_id,receiver_id)
            status_code=200 if res else 500
            return JsonResponse({'status':status,
                                      'message':message},
                                      status=status_code)
        elif response == 'DENY':
            res = UserRequest.objects.handle_move_file_access_request(sender_id,receiver_id,response)
            status = 'success' if res else 'error'
            message = 'Request with sender_id:{} and receiver_id:{} has been successfully declined'.format(sender_id,receiver_id)
            status_code = 200 if res else 500
            return JsonResponse({'status': status,
                                 'message': message},
                                status=status_code)
        else:
            return JsonResponse({'status':'bad request',
                                 'message':'{} is not a valid user request response'
                                 ',choose "ACCEPT/DENY" the next time'.format(response)},status=402)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'},status=500)
def add_new_branch_to_repo(project,new_branch_name=None):
    try:
        owner,repo = get_project_owner_repo(project)
        if not all([owner,repo]):
            return JsonResponse({'status': 'bad request',
                                      'message': 'Internal server error'}, status=403)
        new_sync_branch = new_branch_name
        if new_branch_name is None:
            now = str(datetime.now()).replace(' ', '__').replace(':', '-').replace('.', '')
            new_sync_branch = f'Branch_created_w_Sentinel_Flow_at_{now}' if new_branch_name is None else new_branch_name
        default_branch_name = get_default_branch(owner,repo)
        master_branch_sha = get_branch_sha(owner,repo,default_branch_name)
        headers = {
            "Authorization": f"token {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        data = {
            "ref": f'refs/heads/{new_sync_branch}',
            "sha": master_branch_sha
        }
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs"
        response = requests.post(url,headers=headers,json=data)
        if response.status_code != 201:
            return JsonResponse({'status': 'bad request','message':response.json()},status=402)
        return JsonResponse({'status':'success',
                             'messaage':f'Succesfully added branch {new_sync_branch}'})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error','message': 'Internal server error'},status=500)
def modify_branch_from_repo(project,data):
    try:
        old_name = data.get('branch_name')
        new_name = data.get('new_name')
        if not old_name or not new_name:
            return JsonResponse({'status':'bad request','message':'branch_name and new_name are required'},status=400)
        owner,repo = get_project_owner_repo(project)
        if not all([owner,repo]):
            return JsonResponse({'status': 'bad request','message': 'Internal server error'}, status=403)
        headers = {
            "Authorization": f"token {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        url = f"https://api.github.com/repos/{owner}/{repo}/branches/{old_name}/rename"
        response = requests.post(url,headers=headers,json={"new_name":new_name})
        if response.status_code != 201:
            return JsonResponse({'status':'bad request','message':response.json()},status=402)
        return JsonResponse({'status':'success','message':f'Branch {old_name} renamed to {new_name}'})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
def delete_branch_from_repo(project,data):
    try:
        branch_name = data.get('branch_name')
        if not branch_name:
            return JsonResponse({'status':'bad request','message':'branch_name is required'},status=400)
        owner,repo = get_project_owner_repo(project)
        if not all([owner,repo]):
            return JsonResponse({'status': 'bad request','message': 'Internal server error'}, status=403)
        default_branch_name = get_default_branch(owner,repo)
        if branch_name == default_branch_name:
            return JsonResponse({'status':'bad request','message':'Cannot delete the default branch'},status=400)
        headers = {
            "Authorization": f"token {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{branch_name}"
        response = requests.delete(url,headers=headers)
        if response.status_code != 204:
            return JsonResponse({'status':'bad request','message':response.json() if response.content else 'Could not delete branch'},status=402)
        return JsonResponse({'status':'success','message':f'Branch {branch_name} deleted'})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
@login_required
@csrf_protect
@require_http_methods(["POST","PUT","DELETE"])
@ratelimit(key='user',rate='15/m',block=True)
def api_github_handle_branch_action(request,project,repo):
    method = request.method
    project = get_object_or_404(Project,name=project)
    user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
    visitor_permissions = UserProjectRole.objects.get_role_permissions(user_role, project)
    if not visitor_permissions['can_modify_files']:
        return JsonResponse({'status':'Unauthorized access'},status=403)
    match method:
        case "POST":
            return add_new_branch_to_repo(project)
        case "PUT":
            data = json.loads(request.body)
            return modify_branch_from_repo(project,data)
        case "DELETE":
            data = json.loads(request.body)
            return delete_branch_from_repo(project,data)
        case _:
            return JsonResponse({'status':'bad request'},status=400)