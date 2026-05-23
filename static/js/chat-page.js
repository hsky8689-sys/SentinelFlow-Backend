const message_input = document.getElementById("send-message");
const send_button = document.getElementById("send-button");
const message_display = document.getElementById("message-display");

function getCookie(name) {
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

localStorage.setItem("currentConversationMessages","[]");
localStorage.setItem("chatCurrentPage","0");
localStorage.setItem("chatPageSize","300");
localStorage.setItem("conversationCurrentPage","0");
localStorage.setItem("conversationPageSize","500");

chatSocket.onmessage = function(e) {
    const data = JSON.parse(e.data);
    const message = data['message']; // Mesajul brut venit de la server

    // 2. Aici apelezi "Observer-ul" care randează
    appendMessageToDisplay(message);
};
function appendMessageToDisplay(message) {
    const message_display = document.getElementById("message-display");
    const newMessageDiv = document.createElement("div");

    // Adaugi clasele de CSS
    newMessageDiv.classList.add("message");
    newMessageDiv.textContent = message.content;

    // 3. Îl adaugi jos
    message_display.appendChild(newMessageDiv);

    // 4. Scroll automat jos (doar dacă ești deja aproape de jos!)
    message_display.scrollTop = message_display.scrollHeight;
}
async function sendMessage() {
    const message = message_input.value;
    const conversation_id = window.djangoContext.chat_info.conversation_id;
    const user_1o1 = window.djangoContext.chat_info.current_user_converstaion;
    const response = await fetch('/chat/api/send', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken')
        },
        body: JSON.stringify({
            content: message,
            conversation_id: conversation_id,
            user_1o1: user_1o1
        })
    });
    const result = await response.json();
    if (result.success) {
        const bubble = document.createElement("div");
        bubble.classList.add("message", "sent");
        bubble.innerText = message;

        // 2. Îl adăugăm în container
        const messageDisplay = document.getElementById("message-display");
        messageDisplay.appendChild(bubble);

        // 3. Resetăm input-ul și dăm scroll jos
        message_input.value = "";
        messageDisplay.scrollTop = messageDisplay.scrollHeight;
    }
}
async function loadAllUserConversations(){
    const rawConversationPage = localStorage.getItem("conversationCurrentPage");
    const rawConversationPageSize = localStorage.getItem("conversationPageSize");
    const convPage = JSON.parse(rawConversationPage) || 0;
    const convPageSize = JSON.parse(rawConversationPageSize) || 500;
    try{
        const desiredUrl = `/chat/conversations/?pageNumber=${convPage}&pageSize=${convPageSize}`;
        const response = await fetch(desiredUrl);
        if(!response.ok){
            throw new Error(`Eroare server ${response.status}`)
        }
        const received = await response.json();
        alert(received);
    }catch (error){
        alert(error);
    }
}
async function loadNewConversation(conversationId){
    if(conversationId === null){
        return null;
    }
    window.djangoContext.chat_info.conversation_id=conversationId;
    const rawPageNumber = localStorage.getItem("chatCurrentPage");
    const rawchatPageSize = localStorage.getItem("chatPageSize");
    const rawCurrentLoadedMessages = localStorage.getItem("currentConversationMessages");
    let chatPageNumber = JSON.parse(rawPageNumber) || 0;
    let chatPageSize = JSON.parse(rawchatPageSize) || 300;
    let currentMessages = JSON.parse(rawCurrentLoadedMessages) || [];
    try{
        const desiredUrl = `/chat/api/${conversationId}?pageNumber=${chatPageNumber}&pageSize=${chatPageSize}`;
        const response = await fetch(desiredUrl);
        if(!response.ok){
            throw new Error(`Eroare server ${response.status}`)
        }
        const received = await response.json();
        alert(received);
    }catch (error){
        alert(error);
    }
}
document.addEventListener("DOMContentLoaded", async function() {
    try {
        await loadAllUserConversations();
    } catch (error) {
        console.error("Eroare la inițializare:", error);
    }
});