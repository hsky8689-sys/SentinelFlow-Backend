function getCookie(name){
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (cookie.substring(0, name.length + 1) === (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
}
async function handleProjectJoinRequest(senderId, receiverId, action) {
    if (!action) return;

    try {
        const response = await fetch('/projects/api/requests/project/handle/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                sender_id: senderId,
                receiver_id: receiverId,
                action: action
            })
        });
        const data = await response.json();

        if (data.status === 'success') {
            document.getElementById(`req-project-${senderId}-${receiverId}`).remove();
        } else {
            alert(data.message);
        }
    } catch (error) {
        console.error('Eroare:', error);
    }
}
<<<<<<< HEAD
async function handleFriendRequestFromConversations(senderId, receiverId, action) {
    if (!action) return;

    try {
        const desiredUrl = action === 'accept' ? `/users/${senderId}/accept-friend-request/` : `/users/${senderId}/accept-friend-request/`
        const response = await fetch(desiredUrl, {
            method: 'POST',
=======

async function handleFriendRequest(requestId, action) {
    if (!action) return;

    try {
        const isAccept = action === 'accept';
        const response = await fetch(`/users/friend-requests/${requestId}/`, {
            method: isAccept ? 'PATCH' : 'DELETE',
>>>>>>> 15f441b3cfedabe4288c2707d48d6c3421b903d5
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: isAccept ? JSON.stringify({status: 'accepted'}) : null
        });
        const data = await response.json();

        if (data.status === 'succes' || data.status === 'success') {
            document.getElementById(`req-friend-${requestId}`).remove();
        } else {
            alert(data.message);
        }
    } catch (error) {
        console.error('Eroare:', error);
    }
}

async function handleFileAccessRequest(senderId, receiverId, action, filePath = null) {
    if (!action) return;

    try {
        const response = await fetch(`/api/requests/file/handle/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                'sender_id': senderId,
                'receiver_id': receiverId,
                'action': action,
                'file_path': filePath
            })
        });
        const data = await response.json();

        if (data.status === 'success') {
            document.getElementById(`req-file-${senderId}-${receiverId}`).remove();
        } else {
            alert(data.message);
        }
    } catch (error) {
        console.error('Eroare:', error);
    }
}