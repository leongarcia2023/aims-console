/* Password gate for the static copy of the AIMS console. Same scheme as the DCAF preview:
   PBKDF2-SHA256 to a key, AES-GCM on the page; the key stays in sessionStorage for the tab. */
(() => {
  const base = new URL('.', (document.currentScript && document.currentScript.src) || location.href);
  const keyName = 'aims-console-unlock-v1';
  const unpack = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const pack = bytes => btoa(String.fromCharCode(...bytes));
  let manifestPromise;
  const manifest = () => manifestPromise ||= fetch(new URL('manifest.json', base), {cache: 'no-store'})
    .then(r => { if (!r.ok) throw new Error('The console copy is unavailable right now.'); return r.json(); })
    .catch(e => { manifestPromise = null; throw e; });
  async function keyFor(password, m) {
    const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']);
    return new Uint8Array(await crypto.subtle.deriveBits({name: 'PBKDF2', hash: 'SHA-256', salt: unpack(m.salt), iterations: m.iterations}, material, 256));
  }
  async function decrypt(cipher, iv, raw) {
    const key = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);
    return crypto.subtle.decrypt({name: 'AES-GCM', iv: unpack(iv)}, key, cipher);
  }
  async function verify(raw, m) {
    try { return new TextDecoder().decode(await decrypt(unpack(m.check), m.checkIv, raw)) === 'AIMS console private copy v1'; } catch { return false; }
  }
  async function openApp(raw, m) {
    const response = await fetch(new URL(m.payload, base), {cache: 'no-store'});
    if (!response.ok) throw new Error('The console copy could not be loaded. Try again.');
    const cipher = m.encoding === 'base64' ? unpack((await response.text()).trim()) : await response.arrayBuffer();
    const html = new TextDecoder().decode(await decrypt(cipher, m.iv, raw));
    document.open(); document.write(html); document.close();
  }
  function store(raw) { try { sessionStorage.setItem(keyName, pack(raw)); } catch {} }
  const form = document.getElementById('aims-unlock'), message = document.getElementById('unlock-message'), button = form.querySelector('button');
  let working = false;
  async function enter(raw) {
    const m = await manifest();
    if (!await verify(raw, m)) throw new Error('That password is not correct.');
    store(raw); await openApp(raw, m);
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (working) return; working = true; button.disabled = true; message.textContent = 'Opening';
    try { const m = await manifest(); await enter(await keyFor(form.elements.password.value, m)); }
    catch (error) { message.textContent = error.message; button.disabled = false; working = false; }
  });
  try {
    const saved = sessionStorage.getItem(keyName);
    if (saved) {
      working = true; button.disabled = true; message.textContent = 'Opening';
      enter(unpack(saved)).catch(() => { sessionStorage.removeItem(keyName); working = false; button.disabled = false; message.textContent = ''; });
    }
  } catch {}
})();
