import json

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from django.db import transaction
from django_ratelimit.decorators import ratelimit

from projects.models import Project
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
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        birthday = request.POST['birthday']
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
    user = get_object_or_404(User,username=username)
    profile_stats = {
        "profile_sections":[],
        "teckstack_category":{},
        "profile_projects":[],
    }
    if request.user.username == username:
        profile_stats["profile_sections"] = (UserProfileSection.
                                        objects.
                                        get_user_profile_sections(user,includehidden=True))
    else:
        profile_stats["profile_sections"] = (UserProfileSection.
                                             objects.
                                             get_user_profile_sections(user, includehidden=False))
    profile_stats["techstack_category"] = UserTechnicalSkillSection.objects.get_user_techstack(user)
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
        "email":user.email,
        "id":user.id,
        "profile_sections":[
            {"id": s.id, "name": s.name, "content": s.content, "hidden": s.hidden}
            for s in profile_stats["profile_sections"]
        ],
        "techstack_category":{
            section.name: [skill.name for skill in skills]
            for section, skills in profile_stats["techstack_category"].items()
        },
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
@ratelimit(key='post:username',rate='5/m',method='POST',block=True)
@ratelimit(key='user_or_ip',rate='20/m',method='GET',block=True)
def login_page(request):
    if request.method == "POST":
        if request.user.is_authenticated:
            logout(request)
        username = request.POST['username']
        password = request.POST['password']
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
            logout(request)
        return JsonResponse({'status': 'ready'})
@login_required
@csrf_protect
@require_http_methods(["GET","POST"])
@ratelimit(key='user', rate='60/m', method='GET',block=True)
@ratelimit(key='user', rate='20/m', method='POST',block=True)
def create_project(request):
    if request.method == 'GET':
        return JsonResponse({'status': 'ready', 'user_id': request.user.id})
    elif request.method == 'POST':
        name = request.POST['name']
        description = request.POST['description']
        user_id = request.user.id
        project = Project.objects.create_project(user_id,name, description)
        if project is None:
            return JsonResponse({
                'status': 'error',
                'message': 'Numele de proiect este deja folosit sau invalid (doar litere, cifre, "-" și "_").'
            }, status=400)
        return JsonResponse({
            'status': 'success',
            'project': {'id': project.id, 'name': project.name, 'description': project.description},
        }, status=201)
@login_required
@csrf_protect
@require_POST
@transaction.atomic
@ratelimit(key='user',rate='30/m',block=True)
def api_add_skill(request):
    name = request.POST.get('name')
    section_id = request.POST.get('section_id')
    if not name or not section_id:
        return JsonResponse({'status': 'error', 'message': 'Date lipsă'}, status=400)
    success = UserTechnicalSkill.objects.add_user_skill(name=name, section_id=section_id, user=request.user)
    if success:
        return JsonResponse({'status': 'success','message':'Skill was succsesfully added'},status=200)
    else:
        return JsonResponse({'status': 'error','message':'Skill was already added before, or this section does not belong to you'},status=500)
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