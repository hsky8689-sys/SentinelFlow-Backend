import json

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
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
    return render(request, 'html/search.html', {'user_id': request.user.id})
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
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                birthday=birthday
            )
            login(request,user)
            messages.success(request,f'Bun venit, {user.username}!')
            return redirect('user_profile',username=username)
        except Exception as e:
            messages.error(request,f'Error :{str(e)}')
        messages.success(request, 'Cont creat!')
        return redirect('user_login')

    return render(request, 'html/signup.html')
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
        data = UserProfileData.objects.get(user_id=user.id)
        profile_picture = data.profile_picture
        background_picture = data.background_picture
    except Exception as e:
            pass
    context = {
        "username":user.username,
        "user_avatar":profile_picture,
        "background_picture":background_picture,
        "email":user.email,
        "id":user.id,
        "user":user,
        "profile_sections":profile_stats["profile_sections"],
        "techstack_category":profile_stats["techstack_category"],
        "user_projects":profile_stats["profile_projects"],
        "is_owner":request.user.username == username,
        "sent_to_him": sent_to_him,
        "received_from_him": received_from_him,
        "friends": are_friends,
        "friendship_request_id": friendship_request.id if friendship_request else None,
    }
    return render(request, "html/profile.html", context)
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
            return redirect('users:profile-path', username=username)
        else:
            messages.error(request,'Date incorecte')
            return redirect('user_login')
    else:
        if request.user.is_authenticated:
            logout(request)
        return render(request, "html/login.html")
@login_required
@csrf_protect
@require_http_methods(["GET","POST"])
@ratelimit(key='user', rate='60/m', method='GET',block=True)
@ratelimit(key='user', rate='20/m', method='POST',block=True)
def create_project(request):
    if request.method == 'GET':
        return render(request,'html/create_project.html',{"user_id":request.user.id})
    elif request.method == 'POST':
        name = request.POST['name']
        description = request.POST['description']
        user_id = request.user.id
        project = Project.objects.create_project(user_id,name, description)
        if project is None:
            messages.error(request, 'Numele de proiect este deja folosit sau invalid (doar litere, cifre, "-" și "_").')
            return render(request,'html/create_project.html',{"user_id":request.user.id})
        return acces_profile(request,request.user.username)
@login_required
@csrf_protect
@require_POST
@transaction.atomic
@ratelimit(key='user',rate='30/m',block=True)
def api_add_skill(request):
    name = request.POST.get('name')
    section_id = request.POST.get('section_id')
    print(f"name={name}, section_id={section_id}")

    if not name or not section_id:
        return JsonResponse({'status': 'error', 'message': 'Date lipsă'}, status=400)

    success = UserTechnicalSkill.objects.add_user_skill(name=name, section_id=section_id)
    if success:
        return JsonResponse({'status': 'success','message':'Skill was succsesfully added'},status=200)
    else:
        return JsonResponse({'status': 'error','message':'Skill was already added before'},status=500)
@login_required
@csrf_protect
@require_http_methods(["DELETE"])
@transaction.atomic
@ratelimit(key='user',rate='30/m',block=True)
def api_delete_skill(request,skill_id):
    try:
        skill = UserTechnicalSkill.objects.get(id=skill_id)
        success = UserTechnicalSkill.objects.remove_user_skill(skill)
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
        return render(request, 'html/connections.html', {'context': {'user': request.user,
                'requests': requests}
                })
    except Exception:
        return render(request, 'html/connections.html', {'context': {'user': request.user,
                                                                     'requests': []}
                                                         })