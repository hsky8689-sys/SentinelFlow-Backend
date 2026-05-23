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
 async function goToProjectCreation(){
           try{
        const response = await fetch('/users/create-new-project/', {
            headers: {'X-Requested-With': 'XMLHttpRequest'}
        });
        const data = await response.json();
        if(data.status === 'success'){
            window.location.href = '/users/create-new-project/';
        }
    } catch(error) {
        window.location.href = '/users/create-new-project/';
    }
    }
    async function switchAccount(){
           try{
        const response = await fetch("/login/", {
            headers: {'X-Requested-With': 'XMLHttpRequest'}
        });
        const data = await response.json();
        if(data.status === 'success'){
            window.location.href = "/login/";
        }
    } catch(error) {
        window.location.href = '/login/';
    }
    }
    async function goToInbox(user){
        try{
            const response = await fetch('/users/search/', {
            headers: {'X-Requested-With': 'XMLHttpRequest'}
        });
        }catch (err){
            alert(err);
        }
    }
    async function goToSearch(){
           try{
        const response = await fetch('/users/search/', {
            headers: {'X-Requested-With': 'XMLHttpRequest'}
        });
        const data = await response.json();
        if(data.status === 'success'){
            window.location.href = '/users/search/';
        }
    } catch(error) {
        window.location.href = '/users/search/';
    }
    }
        async function addSkill(categoryId){
        const input = document.querySelector(`input[data-category-id="${categoryId}"]`);
        const name = input.value.trim();

        if(!name){
            alert('Scrie un skill');
            return;
        }
        const formData = new FormData();
        formData.append('name',name);
        formData.append('section_id',categoryId);

        try{
            const response = await fetch(`/users/api/add-skill/`,{
                method:'POST',
                body:formData,
                headers:{
                    'X-CSRFToken':getCookie('csrftoken')
                }
            });

            const data = await response.json();
            if(data.status === 'success'){
                alert('Skill added to '+input.placeholder);
                input.value="";
                location.reload();
            }
        }catch (error){
            alert('Error: '+error)
        }
        }
        async function deleteSkill(skillId) {
            if (confirm('Ștergi skill-ul?')) {
                await fetch(`/api/delete-skill/${skillId}/`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCookie('csrftoken') }
            });
            location.reload();
        }
    }
async function handleFriendRequest(action,id){
    try{
        var desiredUrl = ``;
        switch (action) {
            case 'send':{
                desiredUrl = `/users/${id}/send-friend-request/`;
                break;
            }
            case 'accept':{
                desiredUrl=`/users/${id}/accept-friend-request/`;
                break;
            }
            case 'deny':{
                desiredUrl=`/users/${id}/deny-friend-request/`;
                break;
            }
            case 'remove':{
                desiredUrl = `/users/${id}/remove-friend/`;
                break;
            }
            case 'cancel':{
                desiredUrl = `/users/${id}/cancel-request/`
                break;
            }
            default:{
                alert(`Wrong operation requested :${action}`);
                return;
            }
        }
        const response = await fetch(desiredUrl,{
                method: 'POST',
                headers: { 'X-CSRFToken': getCookie('csrftoken')}
                });
        if(response.ok){
            location.reload();
        }
        else{
            alert(`Request wasn't sent due to error ${response.status}`);
        }
    }catch (error){
        alert(error);
    }
}
async function goToConnections(){
    try{
        const desiredUrl = `/users/connections-page/`;
        const response = await fetch(`/users/connections-page/`,{
            headers: {'X-Requested-With': 'XMLHttpRequest'}
        }
        );
        if(response.ok){
            window.location = desiredUrl;
        }
    }catch (error){
        alert(error);
    }
}
function start1on1Chat(targetUserId) {
    window.location.href = `/chat/?user_1o1=${targetUserId}`;
}