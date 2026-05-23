const repoCache = {}
let activeFileDOMElement = null;
document.addEventListener('DOMContentLoaded', () => {
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
        const desiredUrl = `/projects/api/github/${window.djangoContext.project.owner_username}/${window.djangoContext.project.repo_name}/${path}`;
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
async function displayFileContent(path){
    activeFileDOMElement = path;
    if(repoCache[path]){
        alert('fisierul era in cache');
        console.log(`Afisam cache pentru: ${path}`)
        renderCode(repoCache[path])
        return;
    }
    const desiredUrl = `/projects/api/github/${window.djangoContext.project.owner_username}/${window.djangoContext.project.repo_name}/${path}`;
    try{
        const response = await fetch(desiredUrl);
        const data = await response.json();
        const base64content = data.content.replace(/\s/g, '');
        const decodedContent = decodeURIComponent(escape(atob(base64content)));
        repoCache[path] = decodedContent;
        renderCode(decodedContent);
    }catch(error){
        console.error("Eroare la fișier:", error);
        alert("Nu am putut încărca fișierul.");
    }
}
function renderCode(content) {
    const container = document.getElementById("code-textarea");
    container.value = content;
}
function makeCommit(){
    popUpCommit();
}
async function pushModified(){
    if(activeFileDOMElement!==null){
        const textarea = document.getElementById("code-textarea");
        repoCache[textarea] = textarea.value;
    }
    const rawDirty = window.localStorage.getItem("dirtyFiles");
    let dirty = JSON.parse(rawDirty) || [];
    if(dirty.length === 0){
        alert('La ce dai commit,boss?');
        return;
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
                            'branch':'master',//schimba-l tati....
                            'message':window.localStorage.getItem("commitMessage")
                            })
              });
        if(response.ok){
            window.localStorage.setItem("dirtyFiles",JSON.stringify([]));
            Object.assign(repoCache, {});
            window.location.reload();
        }
        else{
            alert(`Ai futut api callu tata`,response.statusText);
        }
    }catch (error){
        alert(error);
    }
}
function popUpCommit(){
    if (document.getElementById('active-commit-popup')) return;
    const container = document.getElementById('toast-container');
    const rawDirty = window.localStorage.getItem("dirtyFiles");
    const dirty = JSON.parse(rawDirty) || [];
    if(dirty.length === 0){
        alert("You haven't modified any files...");
        return;
    }
    const popup = document.createElement('div');
    popup.className = 'commit-popup';
    popup.id = 'active-commit-popup';
    popup.innerHTML = `
        <h3>🚀 Commit Changes</h3>
        <p style="margin: 0 0 10px 0; font-size: 12px; color: #666;">
            Modifici <strong>${dirty.length}</strong> fișiere.
        </p>
        <input type="text" id="commit-msg-input" placeholder="Scrie mesajul de commit..." autofocus />
        <div class="commit-popup-buttons">
            <button class="btn-cancel" id="commit-cancel">Cancel</button>
            <button class="btn-confirm" id="commit-confirm">Confirm</button>
        </div>
    `;
    container.appendChild(popup);

    // 4. Setăm focus direct pe input ca userul să poată scrie instant
    const input = document.getElementById('commit-msg-input');
    input.focus();

    // 5. Logică pentru butonul CANCEL
    document.getElementById('commit-cancel').onclick = () => {
        popup.remove(); // Îl ștergem pur și simplu
    };

    // 6. Logică pentru butonul CONFIRM
    document.getElementById('commit-confirm').onclick = () => {
        const message = input.value.trim();

        if (message === "") {
            input.style.borderColor = "red";
            input.placeholder = "TREBUIE să pui un mesaj!";
            return;
        }

        // Salvăm mesajul în localStorage
        window.localStorage.setItem("commitMessage", message);
        console.log(`Commit message setat: "${message}"`);

        // Ștergem pop-up-ul din colț
        popup.remove();

        // Apelăm funcția de trimitere propriu-zisă (pushModified) pe care o ai deja definită
        pushModified();
    };

    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            document.getElementById('commit-confirm').click();
        }
    });
}