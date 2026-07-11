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
async function loadPage(context){
    const role = context.role;
    window.localStorage.setItem("newDomains","[]");
    window.localStorage.setItem("removedDomains","[]");
    await loadLanguages(false);
    const domains_div = document.getElementsByClassName("project-domains");
    if(role === 'visitor'){
        console.log('fetching requirements');
        await getProjectRequirements();
    }
}
async function goToMainProjectPage(project_name){
    const desiredUrl = `/projects/project-page/${project_name}/`;
    const bailoutUrl = location.href;
    try{
        const response = await fetch(desiredUrl,{
            headers : {'X-Requested-With': 'XMLHttpRequest'}
        });
         if (response.ok) {
            location.href = desiredUrl;
        } else {
            alert('Nu ai permisiunea sau pagina nu există.');
        }
    }catch (error){
        alert('Couldnt load project page');
        location.href = bailoutUrl;
    }
}
async function goToProjectMembersPage(project_name){
    const desiredUrl = `/projects/project-page/${project_name}/project-members/`;
    const bailoutUrl = location.href;
    try {
        const response = await fetch(desiredUrl, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        if (response.ok) {
            location.href = desiredUrl;
        } else {
            alert('Nu ai permisiunea sau pagina nu există.');
        }
    } catch (error) {
        alert('Eroare de conexiune!');
        location.href = bailoutUrl;
    }
}
async function goToProjectSettings(project_name){
    const desiredUrl = `/projects/project-page/${project_name}/settings/`;
    const bailoutUrl = location.href;
    let proj = window.djangoContext.project;
    const repo_name = proj.repo_name;
    const owner_username = proj.owner_username;
    if(localStorage.getItem("repo_name") === null) localStorage.setItem("repo_name",repo_name);
    if(localStorage.getItem("owner_username") === null) localStorage.setItem("owner_username",owner_username);
    try{
        const response = await fetch(desiredUrl, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        if (response.ok) {
            location.href = desiredUrl;
            if(!window.djangoContext){
                alert("Nu mai ajunge djangoContext in setari");
            }
            else{
                proj.owner_username=localStorage.getItem("repo_name");
                proj.repo_name=localStorage.getItem("owner_username");
                alert(localStorage.getItem("repo_name")+" "+localStorage.getItem("owner_username"));
            }
        } else {
            alert('Nu ai permisiunea sau pagina nu există.');
        }
    }
    catch (err){
        location.href=bailoutUrl;
    }
}
async function copyLinkToClipboard(){
    try{
        await navigator.clipboard.writeText(window.location.href);
        alert(`Link copied to clipboard`);
    }catch(err){
        console.error(`Fail to copy:`,err);
    }
}
async function getProjectRequirements(){
 try{
        const desiredUrl = `/projects/`+window.djangoContext.project.name+`/api-get-project-requirements`;
        const response = await fetch(desiredUrl,{
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        if(response.ok){
            var section = document.getElementsByClassName("project-task-overview").item(0);
            if(section==null){
                return;
            }
            const data = await response.json();
            const requirementsMap = data.requirements;
            var text = '';
            Object.entries(requirementsMap).forEach(([sectionName,reqList]) => {
                text += `<h3>${sectionName}</h3><br>`;
                if (Array.isArray(reqList)) {
                    reqList.forEach(req => {
                        text += `<p>${req.skill}</p><br>`;
                    });
                }
                else{
                    console.log(sectionName+' does not have a list associated with id');
                }
            });
            section.innerHTML = text;
        }
    }catch(err){
        console.error(`Fail to copy:`,err);
    }
}
document.addEventListener('DOMContentLoaded', async () => {
    if (window.djangoContext && window.djangoContext.user) {
        await loadPage(window.djangoContext.user);
    } else {
        console.error('Contextul djangoContext lipsește din pagină!');
    }
});
async function loadLanguages(forceInvalidate=false){
    const selectElement = document.getElementById('languages');
    const projectName = window.djangoContext.project.name;
    selectElement.innerHTML = '<option value="">Loading languages...</option>';
    const url = forceInvalidate ? `/projects/get-available-languages?invalidate=true`
        : `/projects/get-available-languages`;
    try{
        const response = await fetch(url);
        const data = await response.json();
        if(data.status === 'success'){
            selectElement.innerHTML = '';
            data.languages.forEach(lang=>{
                const option = document.createElement('option');
                option.value = lang.id;
                option.textContent = lang.name;
                if(lang.id === 71)
                    option.selected = true;
                selectElement.appendChild(option);
            });
        }
        else{
            selectElement.innerHTML = '<option value="">Error loading languages</option>';
        }
    }catch (error){
        console.log("Se pare că lista de limbaje e coruptă sau veche. Refacem cache-ul...");
        await loadLanguages(true);
    }
}
async function runCode(){
    const consoleOutput = document.getElementById("console-output");
    const editor = document.getElementById("code-textarea");
    const sourceCode = editor.value;
    const languageSelect = document.getElementById("languages");
    const selectedLanguage = languageSelect.value;
    if (!sourceCode.trim()) {
        consoleOutput.innerText = "Te rog să scrii niște cod mai întâi.";
        return;
    }
    consoleOutput.innerText = "Se execută pe server...";
    try {
        const response = await fetch('/projects/api/run-code/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                source_code: sourceCode,
                language_id: selectedLanguage,
                project: window.djangoContext.project.name
            })
        });

        if (response.status === 403) {
            const denied = await response.json();
            consoleOutput.style.color = "#ff4c4c";
            consoleOutput.innerText = denied.error || "Nu ai permisiunea să rulezi cod în acest proiect.";
            return;
        }

        const result = await response.json();

        if (result.stdout) {
            consoleOutput.style.color = "#00ff00";
            consoleOutput.innerText = result.stdout;
        } else if (result.stderr) {
            consoleOutput.style.color = "#ff4c4c";
            consoleOutput.innerText = "Eroare la rulare:\n" + result.stderr;
        } else if (result.compile_output) {
            consoleOutput.style.color = "#ff4c4c";
            consoleOutput.innerText = "Eroare de compilare:\n" + result.compile_output;
        } else {
            consoleOutput.innerText = "Programul a rulat cu succes, dar nu a afișat nimic pe ecran.";
        }

    } catch (error) {
        consoleOutput.style.color = "#ff4c4c";
        consoleOutput.innerText = "Eroare de conexiune cu serverul Django.";
        console.error(error);
    }
}
async function leaveProject(projectId){
    return null;
}
async function requestJoin(projectId) {
    const joinBtn = document.getElementById('join-btn');
    if (!joinBtn) return;

    joinBtn.disabled = true;
    const originalText = joinBtn.textContent;
    joinBtn.textContent = 'Se trimite...';

    try {
        const response = await fetch(`/projects/api/${projectId}/request-join`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken'),
                'Content-Type': 'application/json'
            }
        });

        const data = await response.json();

        if (data.status === 'success') {
            joinBtn.textContent = 'Join Request Pending...';
            joinBtn.classList.remove('btn-primary');
            joinBtn.classList.add('btn-secondary');
        } else {
            alert(data.message || 'A apărut o eroare pe server.');
            joinBtn.disabled = false;
            joinBtn.textContent = originalText;
        }
    } catch (error) {
        console.error('Eroare JS la trimiterea cererii:', error);
        alert('Eroare de rețea. Verifică consola.');
        joinBtn.disabled = false;
        joinBtn.textContent = originalText;
    }
}
async function requestFileAccess(btn) {
    // Extragem datele sigure din data-attributes-urile butonului
    const filepath = btn.getAttribute('data-filepath');
    const projectId = btn.getAttribute('data-project');

    if (!filepath || !projectId) {
        console.error("Lipsesc atributele de pe buton.");
        return;
    }

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = 'Se trimite...';

    try {
        const response = await fetch(`/api/projects/${projectId}/request-file/`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': getCookie('csrftoken'),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                filepath: filepath
            })
        });

        const data = await response.json();

        if (data.status === 'success') {
            btn.textContent = 'Pending...';
            btn.classList.remove('btn-warning');
            btn.classList.add('btn-secondary');
        } else {
            alert('Eroare: ' + data.message);
            btn.disabled = false;
            btn.textContent = originalText;
        }
    } catch (error) {
        console.error('Eroare fetch fișier:', error);
        alert('Eroare de rețea. Verifică consola.');
        btn.disabled = false;
        btn.textContent = originalText;
    }
}