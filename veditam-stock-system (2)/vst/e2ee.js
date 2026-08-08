/* e2ee.js - end-to-end encryption for private chats.
 *
 * How it works
 *   * Every account gets an ECDH P-256 key pair the first time it opens
 *     Messages. The PRIVATE key is generated in the browser and stored only in
 *     this browser (localStorage). Only the PUBLIC key is uploaded.
 *   * Each message gets a fresh random AES-GCM 256 key. The body (and any
 *     attachment bytes) are encrypted with it. That message key is then wrapped
 *     once per participant using ECDH(sender private key, member public key).
 *   * The server therefore stores ciphertext only. Nobody on the server side -
 *     including a Super Admin - has the private keys, so private chats cannot
 *     be read by anyone except the participants.
 *   * Announcements are a public broadcast channel and stay in clear text.
 */
window.E2EE = (function () {
  var subtle = (window.crypto && window.crypto.subtle) || null;
  var me = '';
  try { me = localStorage.getItem('v_username') || ''; } catch (e) {}

  var PRIV_KEY = 'v_e2ee_priv_' + me;
  var PUB_KEY = 'v_e2ee_pub_' + me;
  var myPair = null;              // { priv: CryptoKey, pubJwk: object }
  var pubCache = {};              // username -> CryptoKey
  var secretCache = {};           // username -> derived AES-GCM CryptoKey

  function supported() { return !!subtle; }
  function api(p, m, b) { return window.App.apiCall(p, m || 'GET', b || null); }

  // ---- base64 helpers ------------------------------------------------------
  function b64(buf) {
    var bytes = new Uint8Array(buf), s = '';
    for (var i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }
  function unb64(str) {
    var s = atob(String(str || '')), out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  function utf8(str) { return new TextEncoder().encode(str); }
  function fromUtf8(buf) { return new TextDecoder().decode(buf); }

  // ---- identity ------------------------------------------------------------
  function importPriv(jwk) {
    return subtle.importKey('jwk', jwk, { name: 'ECDH', namedCurve: 'P-256' }, false, ['deriveKey']);
  }
  function importPub(jwk) {
    return subtle.importKey('jwk', jwk, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  }

  /* Loads this browser's key pair, creating and publishing it on first use. */
  function ready() {
    if (!supported()) return Promise.reject(new Error('This browser cannot do end-to-end encryption.'));
    if (myPair) return Promise.resolve(myPair);
    var storedPriv = null, storedPub = null;
    try {
      storedPriv = JSON.parse(localStorage.getItem(PRIV_KEY) || 'null');
      storedPub = JSON.parse(localStorage.getItem(PUB_KEY) || 'null');
    } catch (e) {}

    var chain;
    if (storedPriv && storedPub) {
      chain = importPriv(storedPriv).then(function (priv) {
        return { priv: priv, pubJwk: storedPub };
      });
    } else {
      chain = subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveKey'])
        .then(function (pair) {
          return Promise.all([
            subtle.exportKey('jwk', pair.privateKey),
            subtle.exportKey('jwk', pair.publicKey),
          ]).then(function (jwks) {
            try {
              localStorage.setItem(PRIV_KEY, JSON.stringify(jwks[0]));
              localStorage.setItem(PUB_KEY, JSON.stringify(jwks[1]));
            } catch (e) {}
            return { priv: pair.privateKey, pubJwk: jwks[1] };
          });
        });
    }

    return chain.then(function (pair) {
      myPair = pair;
      pubCache[me.toLowerCase()] = null;   // resolved lazily below
      // Publish (or re-publish) the public half so others can write to us.
      return api('/messaging/keys', 'POST', { public_jwk: JSON.stringify(pair.pubJwk) })
        .catch(function () { return null; })
        .then(function () { return pair; });
    });
  }

  function publicKeyOf(username) {
    var k = String(username || '').toLowerCase();
    if (k === me.toLowerCase() && myPair) return importPub(myPair.pubJwk);
    if (pubCache[k]) return Promise.resolve(pubCache[k]);
    return api('/messaging/keys?users=' + encodeURIComponent(username)).then(function (r) {
      var map = r.data || {}, jwk = null;
      Object.keys(map).forEach(function (name) {
        if (name.toLowerCase() === k && map[name]) { try { jwk = JSON.parse(map[name]); } catch (e) {} }
      });
      if (!jwk) throw new Error(username + ' has not opened Messages yet, so no encryption key exists for them.');
      return importPub(jwk).then(function (key) { pubCache[k] = key; return key; });
    });
  }

  /* Shared AES-GCM key between me and `username` (symmetric both ways). */
  function sharedKey(username) {
    var k = String(username || '').toLowerCase();
    if (secretCache[k]) return Promise.resolve(secretCache[k]);
    return ready().then(function (pair) {
      return publicKeyOf(username).then(function (pub) {
        return subtle.deriveKey({ name: 'ECDH', public: pub }, pair.priv,
          { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
      });
    }).then(function (key) { secretCache[k] = key; return key; });
  }

  function randomIv() { return window.crypto.getRandomValues(new Uint8Array(12)); }

  // ---- encrypt -------------------------------------------------------------
  /* members: everyone who must be able to read it (sender included).
     file: optional { name, type, bytes: ArrayBuffer }
     Returns { body: envelopeJson, attachmentBytes: Uint8Array|null } */
  function encrypt(members, text, file) {
    var people = {};
    (members || []).concat([me]).forEach(function (m) { if (m) people[m] = 1; });
    var recipients = Object.keys(people);
    var msgKey, rawKey;

    return ready()
      .then(function () {
        return subtle.generateKey({ name: 'AES-GCM', length: 256 }, true, ['encrypt', 'decrypt']);
      })
      .then(function (key) {
        msgKey = key;
        return subtle.exportKey('raw', key);
      })
      .then(function (raw) {
        rawKey = raw;
        var jobs = [];
        var env = { v: 1, e2ee: true, alg: 'ECDH-P256+AES-GCM', sender: me, keys: {} };

        // Body
        var bodyIv = randomIv();
        jobs.push(subtle.encrypt({ name: 'AES-GCM', iv: bodyIv }, msgKey, utf8(text || ''))
          .then(function (ct) { env.iv = b64(bodyIv); env.ct = b64(ct); }));

        // Attachment (bytes encrypted with the same message key)
        var attBytes = null;
        if (file && file.bytes) {
          var fileIv = randomIv();
          jobs.push(subtle.encrypt({ name: 'AES-GCM', iv: fileIv }, msgKey, file.bytes)
            .then(function (ct) {
              attBytes = new Uint8Array(ct);
              env.file = { name: file.name, type: file.type || '', iv: b64(fileIv), size: file.bytes.byteLength };
            }));
        }

        // Wrap the message key for every participant.
        recipients.forEach(function (user) {
          jobs.push(sharedKey(user).then(function (shared) {
            var iv = randomIv();
            return subtle.encrypt({ name: 'AES-GCM', iv: iv }, shared, rawKey).then(function (ct) {
              env.keys[user] = { iv: b64(iv), ct: b64(ct) };
            });
          }));
        });

        return Promise.all(jobs).then(function () {
          return { body: JSON.stringify(env), attachmentBytes: attBytes };
        });
      });
  }

  // ---- decrypt -------------------------------------------------------------
  function parse(body) {
    var text = String(body == null ? '' : body).trim();
    if (text.charAt(0) !== '{' || text.indexOf('"e2ee"') === -1) return null;
    try {
      var env = JSON.parse(text);
      return env && env.e2ee ? env : null;
    } catch (e) { return null; }
  }
  function isEncrypted(body) { return !!parse(body); }

  /* Unwraps the per-message AES key for me. */
  function messageKey(env) {
    var slot = null;
    Object.keys(env.keys || {}).forEach(function (user) {
      if (user.toLowerCase() === me.toLowerCase()) slot = env.keys[user];
    });
    if (!slot) return Promise.reject(new Error('not-for-me'));
    var peer = env.sender || me;
    return sharedKey(peer).then(function (shared) {
      return subtle.decrypt({ name: 'AES-GCM', iv: unb64(slot.iv) }, shared, unb64(slot.ct));
    }).then(function (raw) {
      return subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['decrypt']);
    });
  }

  /* Returns { text, file } for an encrypted message, or null if not encrypted. */
  function decryptMessage(body) {
    var env = parse(body);
    if (!env) return Promise.resolve(null);
    return messageKey(env).then(function (key) {
      return subtle.decrypt({ name: 'AES-GCM', iv: unb64(env.iv), }, key, unb64(env.ct))
        .then(function (buf) {
          return { text: fromUtf8(buf), file: env.file || null, key: key };
        });
    });
  }

  /* Decrypts downloaded attachment bytes using the message envelope. */
  function decryptAttachment(body, bytes) {
    var env = parse(body);
    if (!env || !env.file) return Promise.reject(new Error('Attachment is not encrypted.'));
    return messageKey(env).then(function (key) {
      return subtle.decrypt({ name: 'AES-GCM', iv: unb64(env.file.iv) }, key, bytes);
    }).then(function (buf) {
      return { blob: new Blob([buf], { type: env.file.type || 'application/octet-stream' }),
               name: env.file.name || 'attachment' };
    });
  }

  return {
    supported: supported,
    ready: ready,
    encrypt: encrypt,
    isEncrypted: isEncrypted,
    decryptMessage: decryptMessage,
    decryptAttachment: decryptAttachment,
    b64: b64,
    unb64: unb64,
  };
})();
