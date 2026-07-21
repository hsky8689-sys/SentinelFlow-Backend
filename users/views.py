import io
import json

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from devnetwork.caching import cache_manager, UserCacheKey
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from django_ratelimit.decorators import ratelimit
from PIL import Image
import secrets
from decouple import config
from projects.models import Project, ProjectSkillRequirement
from .models import User, UserProfileSection, UserTechnicalSkillSection, UserTechnicalSkill, UserRequest, Friendship, \
    UserProfileData
from .search import SearchManager, SearchFilterData

@login_required
@csrf_protect
@ratelimit(key='user', rate='60/m',method='GET',block=True)
def search_page(request):
    return JsonResponse({'status': 'success', 'user_id': request.user.id})
@login_required
@csrf_protect
@ratelimit(key='user', rate='30/m',method='POST',block=True)
def search_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            query = data.get('query','').lower()
            data = SearchFilterData(user_id=request.user.id,query=query,search_type='ALL',sort_by_date=False,sort_by_relevance=False)
            manager = SearchManager()
            manager.execute_search(data)
            results = manager.get_results_from_search()
            return JsonResponse({
                'status':'success',
                'results':results
            })
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        except Exception as e:
            print(str(e))
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
@ratelimit(key='ip', rate='10/m', method='POST',block=True)
def signup_page(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        birthday = request.POST.get('birthday')
        if not all([username, email, password, birthday]):
            return JsonResponse({
                'status': 'error',
                'message': 'username, email, password and birthday are all required'
            }, status=400)
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            birthday=birthday
        )
        if user is None:
            return JsonResponse({
                'status': 'error',
                'message': 'Could not create account (username/email may already be taken, or a field is invalid)'
            }, status=400)
        login(request, user)
        return JsonResponse({
            'status': 'success',
            'message': f'Welcome, {user.username}!',
            'user': {'id': user.id, 'username': user.username, 'email': user.email},
        }, status=201)

    return JsonResponse({'status': 'ready'})
@login_required
@csrf_protect
@require_GET
@ratelimit(key='user', rate='120/m', block=True)
def acces_profile(request,username):
    user = User.objects.filter(username=username).first()
    if user is None:
        return JsonResponse({'status': 'error', 'message': 'User not found'}, status=404)
    profile_stats = {
        "profile_sections":[],
        "teckstack_category":{},
        "profile_projects":[],
    }
    profile_stats["profile_sections"] = (UserProfileSection.
                                        objects.
                                        get_user_profile_sections(user,
                                                                  includehidden=
                                                                  request.user.username == username))
    profile_stats["techstack_category"] = (UserTechnicalSkillSection.
                                           objects.
                                           get_user_techstack(user))
    profile_stats["profile_projects"] = Project.objects.get_user_projects(user)
    sent_to_him = False
    received_from_him = False
    are_friends = False
    profile_picture = ''
    background_picture = ''
    friendship_request = None
    try:
        friendship_request = UserRequest.objects.find_request(request.user, user).first()
        friendship = Friendship.objects.find_friendship(request.user,user).first()
        if friendship is not None: are_friends = True
        if friendship_request:
            if friendship_request.status == 'pending':
                if friendship_request.sender_id == request.user.id:
                    sent_to_him = True
                else:
                    received_from_him = True
        data = UserProfileData.objects.get_profile_data(user)
        profile_picture = data.profile_picture.url if data.profile_picture else ''
        background_picture = data.background_picture.url if data.background_picture else ''
    except Exception as e:
            pass
    context = {
        "username":user.username,
        "user_avatar":profile_picture,
        "background_picture":background_picture,
        "email":user.email if request.user.username == username else None,
        "id":user.id,
        "profile_sections":[
            {"id": s.id, "name": s.name, "content": s.content, "hidden": s.hidden}
            for s in profile_stats["profile_sections"]
        ],
        "techstack_category": [
        {
            "id": section.id,
            "name": section.name,
            "skills": [{"id": skill.id, "name": skill.name} for skill in skills]
        }
        for section, skills in profile_stats["techstack_category"].items()],
        "user_projects":[
            {"id": p.id, "name": p.name, "description": p.description}
            for p in profile_stats["profile_projects"]
        ],
        "is_owner":request.user.username == username,
        "sent_to_him": sent_to_him,
        "received_from_him": received_from_him,
        "friends": are_friends,
        "friendship_request_id": friendship_request.id if friendship_request else None,
    }
    return JsonResponse({'status': 'success', **context})
@login_required
def inbox_page(request):
    pass
@require_http_methods(["POST","GET"])
@ratelimit(key='ip',rate='10/m',method='POST',block=True)
@ratelimit(key='post:username',rate='20/m',method='POST',block=True)
@ratelimit(key='user_or_ip',rate='20/m',method='GET',block=True)
def login_page(request):
    if request.method == "POST":
        if request.user.is_authenticated:
            return JsonResponse({'status':'bad request','message':'You are already logged in'},status=400)
        data = json.loads(request.body)
        username = data.get('username')
        password = data.get('password')
        if not username or not password:
                return JsonResponse({'status': 'bad request', 'message': 'Missing credentials'}, status=400)
        user = authenticate(request,username=username,password=password)
        if user:
            login(request, user)
            return JsonResponse({
                'status': 'success',
                'user': {'id': user.id, 'username': user.username, 'email': user.email},
            })
        else:
            return JsonResponse({'status': 'error', 'message': 'Date incorecte'}, status=401)
    else:
        if request.user.is_authenticated:
           return JsonResponse({'status':'bad request','message':'You are already logged in'},status=400)
        return JsonResponse({'status': 'ready'},status=200)
@login_required
@require_POST
def logout_page(request):
    logout(request)
    return JsonResponse({'status': 'success', 'message': 'Logged out'}, status=200)
@login_required
@csrf_protect
@require_http_methods(["GET","POST"])
@ratelimit(key='user', rate='60/m', method='GET',block=True)
@ratelimit(key='user', rate='20/m', method='POST',block=True)
def create_project(request):
    if request.method == 'GET':
        return JsonResponse({'status': 'ready', 'user_id': request.user.id})
    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)

        name = data.get('name')
        description = data.get('description')
        if not name or not description:
            return JsonResponse({'status': 'error', 'message': 'name and description are required'}, status=400)

        needed_skills = data.get('needed_skills', {})
        if not isinstance(needed_skills, dict) or not all(
            isinstance(domain, str) and isinstance(skills, list) and all(isinstance(s, str) for s in skills)
            for domain, skills in needed_skills.items()
        ):
            return JsonResponse({
                'status': 'error',
                'message': 'needed_skills must be an object of {domain: [skill, ...]}'
            }, status=400)

        github_repos = data.get('github_repos', [])
        if not isinstance(github_repos, list) or not all(
            isinstance(repo, dict) and repo.get('github_repo_name') and repo.get('github_repo_link')
            for repo in github_repos
        ):
            return JsonResponse({
                'status': 'error',
                'message': 'github_repos must be a list of {github_repo_name, github_repo_link, github_repo_access_token}'
            }, status=400)

        user_id = request.user.id
        project = Project.objects.create_project(user_id, name, description, needed_skills, github_repos)
        if project is None:
            return JsonResponse({
                'status': 'error',
                'message': 'Numele de proiect este deja folosit sau invalid (doar litere, cifre, "-" și "_").'
            }, status=400)
        return JsonResponse({
            'status': 'success',
            'project': {
                'id': project.id,
                'name': project.name,
                'description': project.description,
                'needed_skills': ProjectSkillRequirement.objects.get_requirements_grouped_by_sections(project),
                'github_repos': [
                    {'id': r.id, 'github_repo_name': r.github_repo_name, 'github_repo_link': r.github_repo_link}
                    for r in project.repo_stats.all()
                ],
            },
        }, status=201)
@login_required
@csrf_protect
@require_POST
@transaction.atomic
@ratelimit(key='user',rate='30/m',block=True)
def api_add_skill(request):
    body = json.loads(request.body)
    name = body.get('name')
    section_id = body.get('section_id')
    if not name or not section_id:
        return JsonResponse({'status': 'error', 'message': 'Date lipsă'}, status=400)
    result = UserTechnicalSkill.objects.add_user_skill(name=name, section_id=section_id, user=request.user)
    if result == 'invalid':
        return JsonResponse({'status': 'error', 'message': 'section_id must be a valid id'}, status=400)
    if result == 'not_found':
        return JsonResponse({'status': 'error', 'message': 'Section not found'}, status=404)
    if result == 'duplicate':
        return JsonResponse({'status': 'error', 'message': 'Skill was already added before'}, status=409)
    if result == 'error':
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
    return JsonResponse({
        'status': 'success', 'message': 'Skill was succsesfully added', 'skill_id': result.id
    }, status=200)
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='30/m',block=True)
def api_add_techstack_section(request):
    try:
        body = json.loads(request.body)
        section_name = body.get('section_name')
        skills_names = body.get('skills_names')
        if not section_name:
            return JsonResponse({'status':'bad request','message':'section name is mandatory'},status=400)
        if (skills_names is not None) and (not isinstance(skills_names,list)):
            return JsonResponse({'status':'bad request','message':'you can either provide a LIST of skill names or no skill names at all'},status=400)
        result = UserTechnicalSkillSection.objects.add_user_techstack(name=section_name,
                                                                       user=request.user,
                                                                       skills_names=skills_names)
        if result == 'duplicate':
            return JsonResponse({'status':'error','message':'You already have a techstack section with this name'},status=409)
        if result == 'error':
            return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
        skills_data = [{"id":s.id,"name":s.name} for s in UserTechnicalSkill.objects.get_skills_from_section(result.id)]
        return JsonResponse({'status':'success','message':'section succesfully created',
                             'section_id':result.id,'skills_data':skills_data},status=200)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': 'Internal server error'}, status=500)
@login_required
@csrf_protect
@require_http_methods(["DELETE"])
@transaction.atomic
@ratelimit(key='user',rate='30/m',block=True)
def api_delete_techstack_section(request,section_id):
    try:
        section = UserTechnicalSkillSection.objects.get(id=section_id)
        deleted = UserTechnicalSkillSection.objects.remove_user_techstack(section, request.user)
        if not deleted:
            return JsonResponse({'status':'error','message':'You do not own this techstack section'},status=403)
        return JsonResponse({'status': 'success'})
    except UserTechnicalSkillSection.DoesNotExist:
        return JsonResponse({'status':'error','message':'Section not found'},status=404)
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='30/m',block=True)
def api_add_profile_section(request):
    try:
        body = json.loads(request.body)
        name = body.get('name')
        content = body.get('content')
        hidden = body.get('hidden', False)
        if not name or not content:
            return JsonResponse({'status':'bad request','message':'name and content are mandatory'},status=400)
        if not isinstance(hidden,bool):
            return JsonResponse({'status':'bad request','message':'hidden must be a boolean'},status=400)
        new_section_id = UserProfileSection.objects.create_user_profile_section(user=request.user,name=name,content=content,hidden=hidden)
        if new_section_id is None:
            return JsonResponse({'status': 'error', 'message': 'Could not add profile section'}, status=500)
        return JsonResponse({'status':'success','message':'Profile section created','id':new_section_id},status=200)
    except Exception as e:
        return JsonResponse({'status':'error','message':'Internal server error'},status=500)
@login_required
@csrf_protect
@require_http_methods(["PUT","DELETE"])
@ratelimit(key='user',rate='30/m',block=True)
def api_handle_profile_section(request,section_id):
    match request.method:
        case "PUT":
            try:
                body = json.loads(request.body)
                name = body.get('name')
                content = body.get('content')
                hidden = body.get('hidden', False)
                if not name or not content:
                    return JsonResponse({'status':'bad request','message':'name and content are mandatory'},status=400)
                if not isinstance(hidden,bool):
                    return JsonResponse({'status':'bad request','message':'hidden must be a boolean'},status=400)
                new_section = UserProfileSection(id=section_id,name=name,content=content,hidden=hidden)
                updated = UserProfileSection.objects.update_user_profile_section(new_section,request.user)
                if updated is None:
                    return JsonResponse({'status':'error','message':'Internal server error'},status=500)
                if not updated:
                    return JsonResponse({'status':'error','message':'You do not own this profile section'},status=403)
                return JsonResponse({'status':'success'},status=200)
            except Exception as e:
                return JsonResponse({'status':'error','message':'Internal server error'},status=500)
        case "DELETE":
            try:
                deleted = UserProfileSection.objects.delete_user_profile_section(request.user,section_id)
                if not deleted:
                    return JsonResponse({'status':'error','message':'You do not own this profile section'},status=403)
                return JsonResponse({'status':'success'},status=200)
            except Exception as e:
                return JsonResponse({'status':'error','message':'Internal server error'},status=500)
@login_required
@csrf_protect
@require_http_methods(["DELETE"])
@transaction.atomic
@ratelimit(key='user',rate='30/m',block=True)
def api_delete_skill(request,skill_id):
    try:
        skill = UserTechnicalSkill.objects.get(id=skill_id)
        deleted = UserTechnicalSkill.objects.remove_user_skill(skill, request.user)
        if not deleted:
            return JsonResponse({'status':'error','message':'You do not own this skill'},status=403)
        return JsonResponse({'status': 'success'})
    except UserTechnicalSkill.DoesNotExist:
        return JsonResponse({'status':'error','message':'Skill not found'},status=404)
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_friend_requests(request):
    try:
        data = json.loads(request.body)
        user = User.objects.get(id=data.get('receiver_id'))
        if user == request.user:
            return JsonResponse({'status': 'error', 'message': "Cannot send request to self"}, status=400)
        sent = UserRequest.objects.send_friend_request(request.user, user)
        if sent is None:
            return JsonResponse({'status': 'error', 'message': 'Request already exists or failed'}, status=400)
        return JsonResponse({'status': 'succes', 'code': 200, 'id': sent.id})
    except User.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'User not found'}, status=404)
    except Exception as e:
        print(f"Eroare API: {str(e)}")
        return JsonResponse({'status': 'error', 'message': 'Internal Server Error'}, status=500)
@login_required
@csrf_protect
@require_http_methods(["PATCH","DELETE"])
@ratelimit(key='user',rate='20/m',block=True)
def api_friend_request_detail(request,id):
    try:
        friend_request = UserRequest.objects.filter(id=id,request_type='friend').first()
        if friend_request is None:
            return JsonResponse({'status': 'error', 'message': 'Request does not exist'}, status=404)
        if request.user.id not in (friend_request.sender_id, friend_request.receiver_id):
            return JsonResponse({'status': 'error', 'message': 'Not part of this request'}, status=403)
        match request.method:
            case "PATCH":
                if friend_request.receiver_id != request.user.id:
                    return JsonResponse({'status': 'error', 'message': 'Only the receiver can accept this request'}, status=403)
                if friend_request.status != 'pending':
                    return JsonResponse({'status': 'error', 'message': 'Request has already been handled'}, status=403)
                data = json.loads(request.body or '{}')
                if data.get('status') != 'accepted':
                    return JsonResponse({'status': 'error', 'message': 'Unsupported status transition'}, status=400)
                sent = UserRequest.objects.accept_request(friend_request)
                if sent is None:
                    return JsonResponse({'status': 'error', 'message': 'Request has not been sent'}, status=500)
                UserRequest.objects.remove_request(friend_request)
                return JsonResponse({'status': 'succes', 'message': 'Request accepted'}, status=200)
            case "DELETE":
                UserRequest.objects.remove_request(friend_request)
                return JsonResponse({'status': 'succes', 'message': 'Request was removed'}, status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal Server Error'}, status=500)
@login_required
@csrf_protect
@require_http_methods(["DELETE"])
@transaction.atomic
@ratelimit(key='user',rate='20/m',block=True)
def api_remove_friend(request,removed):
    try:
        removed = User.objects.get(id=removed)
        if removed is None:
            return JsonResponse({'status': 'error', 'message': 'User does not exist'}, status=404)
        friendship = Friendship.objects.find_friendship(request.user,removed)
        if friendship is None:
            return JsonResponse({'status': 'error', 'message': 'Friendship does not exist'}, status=404)
        removed_friendship = Friendship.objects.remove_friendship(request.user,removed)
        if not removed_friendship:
            return JsonResponse({'status': 'error', 'message': 'Friendship not found or already removed'}, status=404)
        friendship_request = UserRequest.objects.find_request(request.user,removed)
        if len(list(friendship_request))>0:
            UserRequest.objects.remove_request(friendship_request.first())
        return JsonResponse({'status': 'succes', 'message': 'Friendship was removed'}, status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status': 'error', 'message': 'Internal Server Error'}, status=500)
@login_required
@csrf_protect
@require_GET
@ratelimit(key='user',rate='60/m',block=True)
def connections_page(request):
    try:
        requests = UserRequest.objects.get_user_requests(request.user)
        serialized_requests = [
            {
                'id': r.id,
                'sender_id': r.sender_id,
                'receiver_id': r.receiver_id,
                'request_type': r.request_type,
                'status': r.status,
                'target': r.target,
                'timestamp': r.timestamp,
            }
            for r in requests
        ]
        return JsonResponse({'status': 'success', 'user_id': request.user.id, 'requests': serialized_requests})
    except Exception:
        return JsonResponse({'status': 'success', 'user_id': request.user.id, 'requests': []})
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_handle_profile_picture_upload(request):
    picture = request.FILES['picture']
    img_data = Image.open(picture)
    if picture.size > config('MAX_PICTURE_SIZE',cast=int):
        return JsonResponse({'status': 'error',
                                  'message':
                                  'Image too large'}, status=400)
    try:
        user = request.user
        img_data.verify()
        if img_data.format not in (["PNG","JPEG"]):
            return JsonResponse({'status':'bad request',
                                 'message':'Unsupported image format'},
                                 status=401)
        profile_data = UserProfileData.objects.get_profile_data(user)
        if profile_data is None:
            return JsonResponse({'status':'error',
                                 'message':'could not get user profile data'},
                                status=500)
        picture.seek(0)
        reencoded_picture = Image.open(picture)
        width, height = reencoded_picture.size

        if width > config('MAX_PROFILE_PIC_W',cast=int):
            return JsonResponse({'status':'bad request',
                                      'message':'Maximum width for profile picture exceeded'},
                                      status=400)
        if width < config('MIN_PROFILE_PIC_W',cast=int):
            return JsonResponse({'status':'bad request',
                                      'message':'Minimum width for profile not respected'},
                                      status=400)
        if height > config('MAX_PROFILE_PIC_H',cast=int):
            return JsonResponse({'status':'bad request',
                                      'message':'Maximum height for profile picture exceeded'},
                                      status=400)
        if height < config('MIN_PROFILE_PIC_H',cast=int):
            return JsonResponse({'status':'bad request',
                                      'message':'Minimum height for profile not respected'},
                                      status=400)

        old_picture_name = profile_data.profile_picture.name
        old_picture_storage = profile_data.profile_picture.storage
        buffer = io.BytesIO()
        reencoded_picture.save(buffer,format=img_data.format)
        buffer.seek(0)
        filename = f'user_{user.id}_{secrets.token_hex(4)}.{img_data.format.lower()}'
        django_file = ContentFile(buffer.read(),name=filename)
        profile_data.profile_picture.save(filename,django_file,save=True)
        cache_manager.delete(UserCacheKey.PROFILE_DATA.format(user_id=user.id))

        if old_picture_name and old_picture_name != 'static/profile_pictures/sbcf-default-avatar.png':
            old_picture_storage.delete(old_picture_name)

        return JsonResponse({'status':'success',
                                  'message':'profile picture successfully uploaded',
                                  'photo_url':profile_data.profile_picture.url},
                                  status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status':'error',
                                  'message':'corrupted file sent'},
                                  status=400)
@login_required
@csrf_protect
@require_POST
@ratelimit(key='user',rate='20/m',block=True)
def api_add_background_picture(request):
    picture = request.FILES['picture']
    img_data = Image.open(picture)
    if picture.size > config('MAX_PICTURE_SIZE',cast=int):
        return JsonResponse({'status': 'error',
                                  'message':
                                  'Image too large'}, status=400)
    try:
        user = request.user
        img_data.verify()
        if img_data.format not in (["PNG","JPEG"]):
            return JsonResponse({'status':'bad request',
                                 'message':'Unsupported image format'},
                                 status=401)
        profile_data = UserProfileData.objects.get_profile_data(user)
        if profile_data is None:
            return JsonResponse({'status':'error',
                                 'message':'could not get user profile data'},
                                status=500)
        picture.seek(0)
        reencoded_picture = Image.open(picture)
        width, height = reencoded_picture.size

        if width > config('MAX_BACKGROUND_PIC_W',cast=int):
            return JsonResponse({'status':'bad request',
                                      'message':'Maximum width for background picture exceeded'},
                                      status=400)
        if width < config('MIN_BACKGROUND_PIC_W',cast=int):
            return JsonResponse({'status':'bad request',
                                      'message':'Minimum width for background not respected'},
                                      status=400)
        if height > config('MAX_BACKGROUND_PIC_H',cast=int):
            return JsonResponse({'status':'bad request',
                                      'message':'Maximum height for background picture exceeded'},
                                      status=400)
        if height < config('MIN_BACKGROUND_PIC_H',cast=int):
            return JsonResponse({'status':'bad request',
                                      'message':'Minimum height for background not respected'},
                                      status=400)

        old_picture_name = profile_data.background_picture.name
        old_picture_storage = profile_data.background_picture.storage
        buffer = io.BytesIO()
        reencoded_picture.save(buffer,format=img_data.format)
        buffer.seek(0)
        filename = f'user_{user.id}_{secrets.token_hex(4)}.{img_data.format.lower()}'
        django_file = ContentFile(buffer.read(),name=filename)
        profile_data.background_picture.save(filename,django_file,save=True)
        cache_manager.delete(UserCacheKey.PROFILE_DATA.format(user_id=user.id))

        if old_picture_name and old_picture_name != 'static/background_pictures/sbcf-default-backgrounds.png':
            old_picture_storage.delete(old_picture_name)

        return JsonResponse({'status':'success',
                                  'message':'background picture successfully uploaded',
                                  'photo_url':profile_data.background_picture.url},
                                  status=200)
    except Exception as e:
        print(str(e))
        return JsonResponse({'status':'error',
                                  'message':'corrupted file sent'},
                                  status=400)

@ensure_csrf_cookie
#@ratelimit(key='ip',rate='20/m',block=True)
def provide_csrf_token(request):
    token = get_token(request)
    return JsonResponse({'status':'success',
                                         'message':'Succesfully provided csrf token',
                                         'csrftoken':token
                                         },status=200)