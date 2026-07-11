import json

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods, require_GET
from django_ratelimit.decorators import ratelimit
from chat.service import ConversationService
from projects.models import Project, UserProjectRole
@login_required
@require_http_methods(["GET"])
@ratelimit(key='user_or_ip',rate='5/s',method='GET')
def load_user_conversations(request):
    try:
        user_id = request.user.id
        if user_id is None:
            return JsonResponse({'error':'User id unspecified'},status=400)
        page_nr = request.GET.get('pageNumber',None)
        if page_nr is None:
            return JsonResponse({'error':'Page number unspecified'},status=400)
        page_size = request.GET.get('pageSize',None)
        if page_size is None:
            return JsonResponse({'error':'Page number unspecified'},status=400)
        page_nr = int(page_nr)
        page_size = int(page_size)
        conversations = ConversationService.load_user_conversations(
            user_id,
            page_nr,
            page_size=page_size
        )
        serialized_messages = [
            {
                "id": conv.id,
                "last_message": conv.last_message_timestamp
            }
            for conv in conversations
        ]
        return JsonResponse({
            'success': True,
            'message': f'Page with index {page_nr} was retrieved',
            'content': serialized_messages
        }, status=200)
    except ValueError:
        return JsonResponse({'error': 'Parameters must be integers'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
@login_required
@require_GET
@ratelimit(key='user_or_ip',rate='5/s',method='GET')
def load_chat_by_id(request,conversation_id):
    try:
        page_nr = request.GET.get('pageNumber',None)
        page_size = request.GET.get('pageSize',None)
        if page_nr is None:
            return JsonResponse({'error':'Page number unspecified'},status=400)
        if page_size is None:
            return JsonResponse({'error':'Page number unspecified'},status=400)
        page_nr = int(page_nr)
        page_size = int(page_size)
        messages = ConversationService.load_conversation_messages(
            conversation_id,
            page_nr,
            page_size=page_size
        )
        serialized_messages = [
            {
                "sender_id": msg.user_id,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None
            }
            for msg in messages
        ]
        return JsonResponse({
            'success': True,
            'message': f'Page with index {page_nr} was retrieved',
            'content': serialized_messages
        }, status=200)
    except ValueError:
        return JsonResponse({'error': 'Parameters must be integers'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
def open_chat_room(request):
    try:
        conv_id = request.GET.get("conv_id")
        user_101 = int(request.GET.get("user_1o1"))

        if conv_id and conv_id != "null":
            return render(request, "html/chat_room.html", {
                "user": request.user,
                "chat_id": conv_id,
                "user_101": -1
            })

        elif user_101 and user_101 != "null":
            existing_conv = ConversationService.check_if_1o1_conversation_exist(request.user.id, user_101)

            final_chat_id = existing_conv.id if existing_conv else -1

            return render(request, "html/chat_room.html", {
                'success': True,
                "user": request.user,
                "chat_id": final_chat_id,
                "user_101": user_101
            })

        # CAZUL 3: A intrat direct pe /chat/ din bara de navigație
        else:
            # Aici poți să returnezi interfața de chat fără niciun panou dreapta deschis
            return render(request, "html/chat_room.html", {
                "user": request.user,
                "chat_id": -1,
                "user_101": -1
            })

    except Exception as e:
        print(str(e))
        return JsonResponse({'error': f'Error loading chat: {str(e)}'}, status=500)

@login_required
@require_http_methods(['POST'])
@ratelimit(key='user_or_ip',rate='5/s',method='POST')
def chat_message_api(request):
        data = json.loads(request.body)
        user_id = request.user.id
        conversation_id = data.get('conversation_id', -1)
        user_1on1 = data.get('user_1o1', -1)
        content = data.get('content', '')
        if content == '' or content is None:
            return JsonResponse({'error':'Messages cannot be empty'},status=404)
        try:
            message, real_conv_id = ConversationService.send_message(
                user_id, conversation_id, content, user_1on1
            )

            channel_layer = get_channel_layer()

            room_group_name = f'chat_{real_conv_id}'

            async_to_sync(channel_layer.group_send)(
                room_group_name,
                {
                    'type': 'chat_message',
                    'message': {
                        'content': message.content,
                        'sender_id': message.user_id,
                    }
                }
            )

            return JsonResponse({
                'success': True,
                'conversation_id': real_conv_id
            }, status=200)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

def _get_project_conversations(request,project_id):
    try:
        project = get_object_or_404(Project, id=project_id)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if role == 'visitor':
            return JsonResponse({'error': 'You are not a member of this project'}, status=403)

        page_nr = request.GET.get('pageNumber',None)
        page_size = request.GET.get('pageSize',None)
        if page_nr is None:
            return JsonResponse({'error':'Page number unspecified'},status=400)
        if page_size is None:
            return JsonResponse({'error':'Page size unspecified'},status=400)
        page_nr = int(page_nr)
        page_size = int(page_size)

        conversations = ConversationService.load_project_conversations(project_id, page_nr, page_size)
        serialized_conversations = [
            {
                "id": conv.id,
                "last_message": conv.last_message_timestamp,
                "is_group": conv.is_group
            }
            for conv in conversations
        ]
        return JsonResponse({
            'success': True,
            'message': f'Page with index {page_nr} was retrieved',
            'content': serialized_conversations
        }, status=200)
    except ValueError:
        return JsonResponse({'error': 'Parameters must be integers'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def _add_project_conversation(request,project_id):
    try:
        project = get_object_or_404(Project, id=project_id)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if role == 'visitor':
            return JsonResponse({'error': 'You are not a member of this project'}, status=403)

        data = json.loads(request.body)
        member_ids = data.get('member_ids', [])
        valid_member_ids = set(
            UserProjectRole.objects.filter(project=project, user_id__in=member_ids).values_list('user_id', flat=True)
        )
        valid_member_ids.add(request.user.id)
        if len(valid_member_ids) < 2:
            return JsonResponse({'error': 'At least one other project member is required'}, status=400)

        conversation_id = ConversationService.create_group_conversation(project, list(valid_member_ids))
        return JsonResponse({
            'success': True,
            'conversation_id': conversation_id,
            'members': list(valid_member_ids)
        }, status=200)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def _delete_project_conversation(request,project_id):
    try:
        project = get_object_or_404(Project, id=project_id)
        role = UserProjectRole.objects.get_user_role_in_project(project, request.user)
        if role == 'visitor':
            return JsonResponse({'error': 'You are not a member of this project'}, status=403)

        data = json.loads(request.body)
        conversation_id = data.get('conversation_id')
        if not conversation_id:
            return JsonResponse({'error': 'conversation_id is required'}, status=400)

        deleted = ConversationService.delete_project_conversation(project_id, conversation_id)
        if not deleted:
            return JsonResponse({'error': 'Conversation not found for this project'}, status=404)
        return JsonResponse({'success': True, 'message': 'Conversation deleted'}, status=200)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
@require_http_methods(["GET","POST","DELETE"])
@ratelimit(key='user_or_ip',rate='80/m',method='GET')
@ratelimit(key='user_or_ip',rate='30/m',method='POST')
@ratelimit(key='user_or_ip',rate='30/m',method='DELETE')
def api_project_conversations(request,project_id):
    match request.method:
        case "GET":
            return _get_project_conversations(request,project_id)
        case "POST":
            return _add_project_conversation(request,project_id)
        case "DELETE":
            return _delete_project_conversation(request,project_id)