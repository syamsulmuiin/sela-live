'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const setup = location.pathname === '/setup';
  const fragment = location.hash.slice(1);
  let token = fragment.startsWith('token=') ? fragment.slice(6) : fragment;
  if (location.hash) history.replaceState(null, '', location.pathname);
  $('auth-title').textContent = setup ? 'Ruangmu dimulai di sini.' : 'Selamat datang kembali.';
  $('auth-description').textContent = setup ? 'Buat satu akun pribadi. Gunakan tautan penyiapan rahasia dari pengelola dan kata sandi minimal 12 karakter.' : 'Masuk untuk melanjutkan percakapan. Akun ini hanya untuk pemilik ruang.';
  $('password').autocomplete = setup ? 'new-password' : 'current-password';
  $('password').minLength = setup ? 12 : 1;
  $('auth-submit').textContent = setup ? 'Buat akun ↗' : 'Masuk ↗';
  $('auth-form').addEventListener('submit', async event => {
    event.preventDefault();
    $('auth-error').hidden = true; $('auth-submit').disabled = true;
    try {
      if (setup && !token) throw new Error('Tautan penyiapan rahasia diperlukan. Minta tautan baru kepada pengelola.');
      const body = {username:$('username').value, password:$('password').value};
      if (setup) body.token = token;
      const response = await fetch(setup ? '/api/setup' : '/api/login', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}, cache:'no-store', body:JSON.stringify(body)});
      $('password').value = '';
      if (response.ok) { token = ''; location.replace(setup ? '/login' : '/'); return; }
      throw new Error(response.status === 429 ? 'Terlalu banyak percobaan. Tunggu 5 menit sebelum mencoba lagi.' : setup ? 'Penyiapan ditolak. Tautan mungkin kedaluwarsa, akun sudah dibuat, atau isian belum sesuai.' : 'Nama pengguna atau kata sandi tidak cocok.');
    } catch (error) { $('auth-error').textContent = error.message || 'Koneksi gagal. Silakan coba lagi.'; $('auth-error').hidden = false; }
    finally { $('auth-submit').disabled = false; }
  });
})();
