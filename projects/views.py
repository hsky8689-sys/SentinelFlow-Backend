import base64
import json

import django.db
import requests
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

import users.views
from devnetwork import settings
from projects.models import Project, UserProjectRole, ProjectDomain, ProjectSkillRequirement, ProjectRequirementSection, \
    ProjectTask, ProjectRole


@login_required
def create_project(request):
    if request.method == 'POST':

        users.views.acces_profile(request,request.user.username)
    else:
        return JsonResponse({'status': 'error',
                      'code' : 404
                      })
@login_required
def open_project_page(request,name):
    project = Project.objects.filter(name=name).first()
    if not project:
        return JsonResponse({'status': 'failed', 'code': 404})
    staff = UserProjectRole.objects.get_all_users_in_project(project)
    user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
    visitor_permissions = UserProjectRole.objects.get_role_permissions(user_role,project)
    project_domains = ProjectDomain.objects.get_project_domains(project)
    owner_username,repo_name='no_github_owner_set','no_github_name_set'
    if project.root_link:
        root_link = project.root_link.split('/')
        owner_username,repo_name = root_link[3],root_link[4]
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
        'roles': list(staff.keys()),
        'domains':list(project_domains),
        'description':project.description,
        'visitor_permissions':visitor_permissions
    }
    return render(request, 'html/project_page.html', {'stats': context_data})
@login_required
def open_project_members_page(request,name):
    project = Project.objects.filter(name=name).first()
    result = UserProjectRole.objects.get_all_users_in_project(project)
    stats = {'members': result, 'project_name': project.name}
    return render(request, 'html/project_members_page.html', {'stats': stats})

@login_required
@csrf_exempt
def open_project_settings(request, name):
    project = get_object_or_404(Project, name=name)
    user_role = UserProjectRole.objects.get_user_role_in_project(project, request.user)

    context_data = {
        'project_name': project.name,
        'project_id': project.id,
        'role': user_role,
        'user_username': request.user.username,
    }
    return render(request, 'html/project_settings_page.html', {'stats': context_data})
@login_required
def send_project_join_request(request,project):
    pass
@require_http_methods(["GET"])
@csrf_exempt
def api_get_project_domains(request,name):
    try:
        project = get_object_or_404(Project,name=name)
        domains = ProjectDomain.objects.filter(project_id=project.id)
        return JsonResponse({'status':'success','domains':list(domains.values())})
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 500})
@require_http_methods(["POST"])
@csrf_exempt
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
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 500})
@require_http_methods(["POST"])
@csrf_exempt
def api_delete_project_domains(request,name):
    try:
        if request.method == 'POST':
            project = get_object_or_404(Project, name=name)
            role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
            if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
                data = json.loads(request.body)
                domains = data.get('removedDomains', [])
                succes = ProjectDomain.objects.remove_domains_from_project(project, domains)
                return JsonResponse({'status': 'succes' if len(succes) == len(domains) else 'error',
                                     'code': 200 if len(succes) == len(domains) else 404
                                     })
            else:
                return JsonResponse({'status': 'Unauthorized access', 'code': 403})
    except Exception as e:
        print(str(e))
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 500})
@require_http_methods(["GET"])
@csrf_exempt
def api_get_project_requirements(request,name):
    try:
        project = get_object_or_404(Project,name=name)
        succes = ProjectSkillRequirement.objects.get_requirements_grouped_by_sections(project)
        return JsonResponse({'status':'succes','requirements':succes})
    except Exception as e:
        print(str(e))
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 404})
@require_http_methods(["POST"])
@csrf_exempt
def api_add_project_requirements(request,name):
    try:
        project = get_object_or_404(Project, name=name)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            requirements = data.get('newRequirements',[])
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
                manager.add_skill_requirements(section,batches[key])
            return JsonResponse({'status':'succes'})
        else:
            return JsonResponse({'status': 'Unauthorized access', 'code': 403})
    except Exception as e:
        print(str(e))
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 404})
@require_http_methods(["POST"])
@csrf_exempt
def api_remove_project_requirements(request,name):
    try:
        project = get_object_or_404(Project, name=name)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            requirements = data.get('removedRequirements',[])
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
                manager.remove_skill_requirements(section,batches[key])
            return JsonResponse({'status':'succes'})
        else:
            return JsonResponse({'status': 'Unauthorized access', 'code': 403})
    except Exception as e:
        print(str(e))
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 404})
@require_http_methods(["POST"])
@csrf_exempt
def api_remove_project_sections(request,name):
    try:
        project = get_object_or_404(Project, name=name)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            requirements = data.get('removedSections',[])
            ProjectRequirementSection.objects.remove_requirement_sections(project,requirements)
            return JsonResponse({'status':'succes'})
        else:
            return JsonResponse({'status': 'Unauthorized access', 'code': 403})
    except Exception as e:
        print(str(e))
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 404})
@require_http_methods(["POST"])
@csrf_exempt
def api_add_project_sections(request,name):
    try:
        project = get_object_or_404(Project, name=name)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            requirements = data.get('newSections',[])
            ProjectRequirementSection.objects.add_requirement_sections(project,requirements)
            return JsonResponse({'status':'succes'})
        else:
            return JsonResponse({'status': 'Unauthorized access', 'code': 403})
    except Exception as e:
        print(str(e))
    except django.db.DatabaseError:
        return JsonResponse({'status': 'error', 'code': 404})
@csrf_exempt
@require_http_methods(["GET"])
def api_get_project_tasks(request,name):
    try:
        project = get_object_or_404(Project, name=name)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            tasks = ProjectTask.objects.get_project_tasks(project).values()
            return JsonResponse({'status': 'succes','tasks':list(tasks)})
        else:
            return JsonResponse({'status': 'Unauthorized access', 'code': 403})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': str(e), 'code': 404})
@csrf_exempt
@require_http_methods(["POST"])
def api_add_project_task(request,name):
    try:
        data = json.loads(request.body)
        project = Project.objects.get(name=name)
        if project is None:
            return JsonResponse({'status':'Error','message':'Project does not exist','code':404})
        title = data.get('title')
        description = data.get('description')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        return ProjectTask.objects.add_task_to_project(project,title,description,start_date,end_date)
    except Exception as e:
        print(str(e))
@csrf_exempt
@require_http_methods(["DELETE"])
def api_remove_project_tasks(request,name):
    try:
        project = Project.objects.get(name=name)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            data = json.loads(request.body)
            requirements = data.get('removedTasks', [])
            ProjectTask.objects.remove_tasks_from_project(requirements)
            return JsonResponse({'status':'succes','message':200})
        else:
            return JsonResponse({'status': 'Unauthorized access', 'code': 403})
    except Exception as e:
        return JsonResponse({'status':'error','message':str(e),'code':405})
@csrf_exempt
@require_http_methods(["GET"])
def api_get_project_roles(request,name):
    try:
        project = Project.objects.get(name=name)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if UserProjectRole.objects.get_role_permissions(role, project)['can_change_project_settings']:
            project_roles = ProjectRole.objects.get_project_roles(project).values()
            return JsonResponse({'status':'succes','code':200,'roles':list(project_roles)})
        else:
            return JsonResponse({'status': 'Unauthorized access', 'code': 403})
    except Exception as e:
        print(str(e))
        return JsonResponse({'status':'error','code':''})

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
def handle_file_content(request,owner, repo, path):
    cache_key = f"github_file_{owner}_{repo}_{path.replace('/', '_')}"
    cached_file = cache.get(cache_key)
    if cached_file:
        return JsonResponse(cached_file, safe=False)

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if hasattr(settings, 'GITHUB_TOKEN'):
        headers["Authorization"] = f"token {settings.GITHUB_TOKEN}"

    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        cache.set(cache_key, r.json(), timeout=3600)
        return JsonResponse(r.json(), safe=False)
    return JsonResponse(r.json(), status=r.status_code, safe=False)
@login_required
def invalidate_repo_cache(repo:str,owner:str):
    try:
        print(len([k for k in cache.keys("*") if (repo in k and owner in k)]))
        print("-"*30)
    except Exception as e:
        print(str(e))
@login_required
def github_proxy_view(request, owner, repo, path=""):
    #invalidate_repo_cache(repo,owner)
    if path != "" and '.' in path.split('/')[-1]:
        return handle_file_content(request,owner, repo, path)

    branch = "main"
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
def proxy_run_code(request):
    if request.method != "POST":
        return JsonResponse({'error':'non post requests not allowed'},status=405)
    try:
        body = json.loads(request.body)
        source_code = body.get("source_code")
        language_id = body.get("language_id",71)

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
def request_file_open(request):
    pass
@login_required
@require_http_methods(["POST"])
def push_files(request):
    invalidate_repo_cache()
    try:
        data = json.loads(request.body)
        files = data.get('files',{})
        repo = data.get('repo')
        owner = data.get('owner')
        branch = data.get('branch')
        default_msg = data.get('message','')
        if default_msg is None or default_msg == '':
            return JsonResponse({'error':'cannot push with no message'},status=400)
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
            if errors:
                errors.append({'path':path,'error': put_res.json()})

            cache.delete(f"repo_tree_{owner}_{repo}")
            if errors:
                return JsonResponse({'status': 'partial_error', 'errors': errors}, status=400)
            return JsonResponse({'status': 'success'})
    except Exception as e:
        print(str(e))
        return JsonResponse({'error':str(e)},status=500)