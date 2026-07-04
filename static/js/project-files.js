const repoCache = {}
let activeFileDOMElement = null;
let currentBranch = null;
document.addEventListener('DOMContentLoaded', () => {
    const branchSelect = document.getElementById('branch-select');
    if(branchSelect) currentBranch = branchSelect.value;
    const data = loadDirectory();
    window.localStorage.setItem("dirtyFiles",JSON.stringify([]));
    window.localStorage.setItem("commitMessage","");
    const textarea = document.getElementById('code-textarea');
    textarea.addEventListener('input',function(){
    if(activeFileDOMElement==null)return;
        const rawDirty = window.localStorage.getItem("dirtyFiles");
        let dirty = JSON.parse(rawDirty) || [];
        const isAlreadyDirty = dirty.includes(activeFileDOMElement);
        if(!isAlreadyDirty){
            dirty.push(activeFileDOMElement);
            window.localStorage.setItem("dirtyFiles",JSON.stringify(dirty));
            console.log("Fisiere modificate",dirty);
        }
    });
});
async function loadDirectory(path = ""){
    if(repoCache[path]){
        console.log(`Afisam cache pentru: ${path}`)
        renderExplorer(repoCache[path],path)
        return;
    }
    try{
        const desiredUrl = `/projects/api/github/${window.djangoContext.project.owner_username}/${window.djangoContext.project.repo_name}/${path}?branch=${encodeURIComponent(currentBranch || '')}`;
        const response = await fetch(desiredUrl);
        const data = await response.json();
        repoCache[path] = data;
        renderExplorer(data,path);
        return data;
    }catch(error){
        console.error("Eroare la fetch:",error);
        return "";
    }
}
function onBranchChange(newBranch){
    currentBranch = newBranch;
    Object.keys(repoCache).forEach(key => delete repoCache[key]);
    activeFileDOMElement = null;
    renderCode('');
    updateActionButtonsVisibility(null);
    loadDirectory('');
}
function renderExplorer(items,currentPath){
        const container = document.getElementById("project-structure");
        container.innerHTML = "";
        if(currentPath !== ""){
            const backBtn = document.createElement('div');
            backBtn.innerText = '< BACK';
            backBtn.onclick = () => {
                const parts = currentPath.split('/');
                parts.pop();
                loadDirectory(parts.join('/'));
            };
            container.appendChild(backBtn);
        }
        const textarea = document.getElementById("code-textarea");
        items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'explorer-item';
            div.id = item.name;
            div.innerText = (item.type === 'dir' ? "📁 " : "📄 ") + item.name;
            div.onclick = () => {
            if (item.type === 'dir') {
                    loadDirectory(item.path)
                } else {
                    if(activeFileDOMElement!=null) repoCache[activeFileDOMElement] = textarea.value;
                    displayFileContent(item.path)
                }
            }
            container.appendChild(div);
        });
}
function hasFileAccess(path){
    const permissions = (window.djangoContext && window.djangoContext.user && window.djangoContext.user.file_permissions) || {};
    return permissions[path] === 'ACCESS';
}
function updateActionButtonsVisibility(path){
    const commitBtn = document.getElementById('commit-changes-btn');
    const requestAccessBtn = document.getElementById('request-access-btn');
    const accessGranted = hasFileAccess(path);
    if(commitBtn) commitBtn.style.display = accessGranted ? 'inline-block' : 'none';
    if(requestAccessBtn) requestAccessBtn.style.display = accessGranted ? 'none' : 'inline-block';
}
function requestAccessToActiveFile(){
    if(activeFileDOMElement === null) return;
    addToStoredList("requestedForAccess",activeFileDOMElement);
    popUpCommit();
}
async function displayFileContent(path){
    activeFileDOMElement = path;
    updateActionButtonsVisibility(path);

    if(repoCache[path]){
        console.log(`Afisam cache pentru: ${path}`)
        renderCode(repoCache[path])
        requestFileWriteAccess(path);
        return;
    }
    const desiredUrl = `/projects/api/github/${window.djangoContext.project.owner_username}/${window.djangoContext.project.repo_name}/${path}?branch=${encodeURIComponent(currentBranch || '')}`;
    try{
        const response = await fetch(desiredUrl);
        if(response.status === 403){
            renderCode('');
            const commitBtn = document.getElementById('commit-changes-btn');
            const requestAccessBtn = document.getElementById('request-access-btn');
            if(commitBtn) commitBtn.style.display = 'none';
            if(requestAccessBtn) requestAccessBtn.style.display = 'inline-block';
            return;
        }
        if(!response.ok){
            const errorBody = await response.json().catch(() => ({}));
            console.error("Eroare la fișier:", response.status, errorBody);
            renderCode('');
            alert(`Nu am putut încărca fișierul (${response.status}).`);
            return;
        }
        const data = await response.json();
        const base64content = data.content.replace(/\s/g, '');
        const decodedContent = decodeURIComponent(escape(atob(base64content)));
        repoCache[path] = decodedContent;
        renderCode(decodedContent);
        requestFileWriteAccess(path);
    }catch(error){
        console.error("Eroare la fișier:", error);
        alert("Nu am putut încărca fișierul.");
    }
}
async function requestFileWriteAccess(path){
    try{
        const response = await fetch('/projects/api/requests/file-writers/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken'),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                project: window.djangoContext.project.name,
                file_url: path
            })
        });
        const data = await response.json().catch(() => ({}));
        if(response.ok && data.status === 'success'){
            if(data.message === 'Request was successfully sent'){
                alert('Fișierul este deschis de altcineva. Am trimis o cerere de permisiune.');
            }
        }else{
            console.error('Nu am putut obține acces de scriere:', data.message || response.statusText);
        }
    }catch(error){
        console.error('Eroare la cererea de acces de scriere:', error);
    }
}
function renderCode(content) {
    const container = document.getElementById("code-textarea");
    container.value = content;
}
function getStoredList(key){
    return JSON.parse(window.localStorage.getItem(key) || "[]");
}
function setStoredList(key,list){
    window.localStorage.setItem(key,JSON.stringify(list));
}
function addToStoredList(key,path){
    const list = getStoredList(key);
    if(!list.includes(path)){
        list.push(path);
        setStoredList(key,list);
    }
    return list;
}
function removeFromStoredList(key,path){
    const list = getStoredList(key).filter(p => p !== path);
    setStoredList(key,list);
    return list;
}
function makeCommit(){
    popUpCommit();
}
async function pushModified(){
    if(activeFileDOMElement!==null){
        const textarea = document.getElementById("code-textarea");
        repoCache[textarea] = textarea.value;
    }
    const dirty = getStoredList("dirtyFiles");
    if(dirty.length === 0){
        return true;
    }
    var dirtyFiles = {};
    dirty.forEach(filePath=> {
         if(repoCache && repoCache[filePath] !== undefined)
            dirtyFiles[filePath] =repoCache[filePath];
     }
    );
    try{
        const response = await fetch('/projects/api/github/push-files/',
        {
                method : 'POST',
                headers: {
                    'X-CSRFToken': getCookie('csrftoken'),
                    'Content-Type':'Application/JSON'
                },
                body:JSON.stringify({
                            'files':dirtyFiles,
                            'repo':window.djangoContext.project.repo_name,
                            'owner':window.djangoContext.project.owner_username,
                            'branch':currentBranch,
                            'project':window.djangoContext.project.id,
                            'message':window.localStorage.getItem("commitMessage")
                            })
              });
        if(response.ok){
            setStoredList("dirtyFiles",[]);
            return true;
        }
        else{
            alert(`Eroare la push: ${response.statusText}`);
            return false;
        }
    }catch (error){
        alert(error);
        return false;
    }
}
async function sendAccessRequests(){
    const requestedAccess = getStoredList("requestedForAccess");
    if(requestedAccess.length === 0){
        return true;
    }
    try{
        const response = await fetch('/projects/api/request-file-access/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken'),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                project_id: window.djangoContext.project.id,
                file_urls: requestedAccess
            })
        });
        if(response.ok || response.status === 206){
            setStoredList("requestedForAccess",[]);
            return true;
        }
        const errorData = await response.json().catch(() => ({}));
        alert(`Eroare la cererea de acces: ${errorData.error || response.statusText}`);
        return false;
    }catch (error){
        alert(error);
        return false;
    }
}
async function commitChanges(){
    const dirty = getStoredList("dirtyFiles");
    const requestedAccess = getStoredList("requestedForAccess");
    const pushOk = dirty.length > 0 ? await pushModified() : true;
    const accessOk = requestedAccess.length > 0 ? await sendAccessRequests() : true;
    if(pushOk && accessOk){
        Object.assign(repoCache, {});
        window.location.reload();
    }
}
function popUpCommit(){
    if (document.getElementById('active-commit-popup')) return;
    const dirty = getStoredList("dirtyFiles");
    const requestedAccess = getStoredList("requestedForAccess");
    if(dirty.length === 0 && requestedAccess.length === 0){
        alert("Nu ai modificat niciun fișier și nu ai cerut acces la niciunul...");
        return;
    }
    renderCommitPopup();
}
function buildCommitFileListHtml(listKey, paths, emptyLabel){
    if(paths.length === 0){
        return `<li><em>${emptyLabel}</em></li>`;
    }
    return paths.map(path => `
        <li style="display:flex; align-items:center; justify-content:space-between; gap:8px;">
            <span>${path}</span>
            <button class="btn-remove-file" data-list="${listKey}" data-path="${path}"
                    style="background:#dc3545; color:white; border:none; cursor:pointer; padding:2px 8px;">X</button>
        </li>
    `).join('');
}
function renderCommitPopup(){
    const existing = document.getElementById('active-commit-popup');
    if(existing) existing.remove();

    const dirty = getStoredList("dirtyFiles");
    const requestedAccess = getStoredList("requestedForAccess");
    if(dirty.length === 0 && requestedAccess.length === 0){
        return;
    }

    const container = document.getElementById('toast-container');
    const popup = document.createElement('div');
    popup.className = 'commit-popup';
    popup.id = 'active-commit-popup';
    popup.innerHTML = `
        <h3>🚀 Commit Changes</h3>
        <p style="margin: 10px 0 5px 0; font-size: 12px; color: #666;">Fișiere modificate (${dirty.length}):</p>
        <ul class="commit-popup-files" style="list-style:none; padding:0; margin:0;">
            ${buildCommitFileListHtml('dirty', dirty, 'Niciun fișier modificat.')}
        </ul>
        <p style="margin: 10px 0 5px 0; font-size: 12px; color: #666;">Cereri de acces (${requestedAccess.length}):</p>
        <ul class="commit-popup-files" style="list-style:none; padding:0; margin:0;">
            ${buildCommitFileListHtml('access', requestedAccess, 'Niciun acces cerut.')}
        </ul>
        <input type="text" id="commit-msg-input" placeholder="Scrie mesajul de commit..." autofocus />
        <div class="commit-popup-buttons">
            <button class="btn-cancel" id="commit-cancel">Cancel</button>
            <button class="btn-confirm" id="commit-confirm">Confirm</button>
        </div>
    `;
    container.appendChild(popup);

    popup.querySelectorAll('.btn-remove-file').forEach(btn => {
        btn.onclick = () => {
            const path = btn.dataset.path;
            const listKey = btn.dataset.list === 'dirty' ? 'dirtyFiles' : 'requestedForAccess';
            removeFromStoredList(listKey, path);
            renderCommitPopup();
        };
    });

    const input = document.getElementById('commit-msg-input');
    input.focus();

    document.getElementById('commit-cancel').onclick = () => {
        popup.remove();
    };

    document.getElementById('commit-confirm').onclick = async () => {
        const message = input.value.trim();
        const currentDirty = getStoredList("dirtyFiles");

        if (currentDirty.length > 0 && message === "") {
            input.style.borderColor = "red";
            input.placeholder = "TREBUIE să pui un mesaj!";
            return;
        }

        window.localStorage.setItem("commitMessage", message);
        popup.remove();
        await commitChanges();
    };

    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            document.getElementById('commit-confirm').click();
        }
    });
}
class FileAccessObserver{
    constructor() {
        this.listeners=[];
        this.currentInterval=null;
    }
    subscribe(callback){
        this.listeners.push(callback);
    }
    notify(statusData){
        this.listeners.forEach(callback=>callback(statusData));
    }
    startWatching(filePath,projectId){
        this.stopWatching();
        this.currentInterval= setInterval(async ()=>{
            try{
                const response = await fetch(`/api/check-file-status/${projectId}/?path=${filePath}`);
                if (response.status === 403 || response.status === 423) {
                    this.notify({ hasAccess: false, reason: response.status === 403 ? 'revoked' : 'locked' });
                    this.stopWatching();
                }
            }catch (error){
                console.error(`Error ${error} at observer`);
            }
        },5000);
    }
    stopWatching(){
        if(this.currentInterval){
            clearInterval(this.currentInterval);
        }
    }
}
const fileObserver = new FileAccessObserver();

fileObserver.subscribe((status) => {
    if (!status.hasAccess) {
        document.getElementById('code-editor-area').style.display = 'none';
        document.getElementById('no-access-banner').style.display = 'block';

        const motiv = status.reason === 'locked' ? 'Fișierul a fost blocat de alt utilizator.' : 'Ți-a fost revocat accesul.';
        alert(`Atenție: ${motiv}`);
    }
});