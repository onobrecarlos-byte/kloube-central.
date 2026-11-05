document.getElementById('login-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const button=document.getElementById('btn-login-submit');
    const error=document.getElementById('login-error-box');
    button.disabled=true;error.style.display='none';
    try{
        const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('login-email').value.trim(),password:document.getElementById('login-password').value})});
        const data=await response.json();
        if(!data.ok)throw new Error(data.error||'Não foi possível entrar.');
        localStorage.setItem('kloube_auth_user_v1',data.user.email);
        location.replace('/');
    }catch(exc){error.textContent=exc.message;error.style.display='block';}
    finally{button.disabled=false;}
});

const loginView=document.getElementById('auth-login-view'),resetView=document.getElementById('auth-reset-view');
function recoveryMessage(message){const box=document.getElementById('reset-error-box');box.textContent=message;box.style.display='block';}
document.getElementById('btn-forgot-password').addEventListener('click',()=>{
    loginView.style.display='none';resetView.style.display='block';
    document.getElementById('reset-email').value=document.getElementById('login-email').value.trim();
    document.getElementById('reset-error-box').style.display='none';
    document.getElementById('reset-email').focus();
});
document.getElementById('btn-back-to-login').addEventListener('click',()=>{resetView.style.display='none';loginView.style.display='block';});
document.getElementById('btn-send-reset-code').addEventListener('click',async()=>{
    const button=document.getElementById('btn-send-reset-code');button.disabled=true;
    try{
        const response=await fetch('/api/auth/send-reset-code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('reset-email').value.trim()})});
        const data=await response.json();recoveryMessage(data.ok?data.message:data.error);
    }catch(error){recoveryMessage('Não foi possível comunicar com o servidor.');}finally{button.disabled=false;}
});
document.getElementById('reset-password-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const password=document.getElementById('reset-new-password').value;
    if(password!==document.getElementById('reset-confirm-password').value){recoveryMessage('As senhas não coincidem.');return;}
    const button=document.getElementById('btn-reset-submit');button.disabled=true;
    try{
        const response=await fetch('/api/auth/reset-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('reset-email').value.trim(),code:document.getElementById('reset-code').value.trim(),new_password:password})});
        const data=await response.json();
        if(!data.ok){recoveryMessage(data.error);return;}
        event.target.reset();resetView.style.display='none';loginView.style.display='block';
        const box=document.getElementById('login-error-box');box.textContent=data.message;box.style.display='block';
    }catch(error){recoveryMessage('Não foi possível comunicar com o servidor.');}finally{button.disabled=false;}
});
