import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from chat.service import ConversationService
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
@require_http_methods(["GET"])
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

        # CAZUL 1: Există deja o conversație (Grup sau 1-la-1 vechi)
        if conv_id and conv_id != "null":
            return render(request, "html/chat_room.html", {
                "user": request.user,
                "chat_id": conv_id,
                "user_101": -1  # Nu mai contează cine e userul, camera dictează
            })

        # CAZUL 2: Situația de nișă - Vrem să vorbim cu cineva specific, dar nu știm dacă avem cameră
        elif user_101 and user_101 != "null":
            # Verificăm dacă nu cumva s-a creat vreo cameră între timp în spate
            # (Poate i-a scris el primul acum 5 minute și tu abia ai dat refresh)
            existing_conv = ConversationService.check_if_1o1_conversation_exist(request.user.id, user_101)

            # Dacă există, îi dăm ID-ul camerei. Dacă nu, îi dăm -1 (să știe JS-ul să o creeze lazy la primul Send)
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

            return JsonResponse({
                'success': True,
                'conversation_id': real_conv_id
            }, status=200)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)