/* =========================================================
   Shared by every page: helpers, 3D background, card tilt
   ========================================================= */

function esc(t) {
  const d = document.createElement("div");
  d.textContent = t == null ? "" : t;
  return d.innerHTML;
}

function toast(msg, ok = true) {
  const t = document.createElement("div");
  t.textContent = msg;
  t.style.cssText =
    "position:fixed;right:24px;bottom:24px;z-index:100;padding:14px 20px;max-width:340px;" +
    "font-family:'Press Start 2P',monospace;font-size:10px;line-height:1.6;" +
    "background:#0a1220;border:3px solid " + (ok ? "#00ffa3" : "#ff4d4d") + ";" +
    "color:" + (ok ? "#00ffa3" : "#ff4d4d") + ";" +
    "box-shadow:5px 5px 0 #0a3a66;transition:all .4s;transform:translateY(20px);opacity:0;";
  document.body.appendChild(t);
  requestAnimationFrame(() => { t.style.transform = "none"; t.style.opacity = "1"; });
  setTimeout(() => { t.style.opacity = "0"; t.style.transform = "translateY(20px)"; }, 2600);
  setTimeout(() => t.remove(), 3100);
}

/* talk to the Flask backend */
async function api(url, method = "GET", body = null) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : null
  });
  let data = {};
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401) { location.href = "/"; throw new Error("login required"); }
  if (!res.ok) throw new Error(data.error || "Something went wrong");
  return data;
}

/* every protected page calls this first */
async function requireRole(...roles) {
  const me = await api("/api/me");
  if (!roles.includes(me.role)) { location.href = "/"; throw new Error("wrong role"); }
  return me;
}

async function logout() {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/";
}

/* ---------- background layers ---------- */
function addLayers() {
  document.body.insertAdjacentHTML("afterbegin",
    '<canvas id="bg3d"></canvas>' +
    '<div class="bg-orb orb1"></div><div class="bg-orb orb2"></div><div class="bg-orb orb3"></div>' +
    '<div class="cybergrid"></div><div class="beam"></div><div class="scanlines"></div>');
}

/* ---------- 3D scene (Three.js) ---------- */
function init3D(hero) {
  if (typeof THREE === "undefined") return;
  try {
    const canvas = document.getElementById("bg3d");
    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 200);
    camera.position.z = 30;

    const colors = [0x00e5ff, 0x00ffa3, 0x3d7bff, 0x8a5cff];
    const geos = [
      new THREE.IcosahedronGeometry(2.2, 0),
      new THREE.OctahedronGeometry(2.4, 0),
      new THREE.BoxGeometry(3, 3, 3),
      new THREE.TorusGeometry(2, 0.6, 8, 20),
      new THREE.TetrahedronGeometry(2.6, 0)
    ];

    // floating wireframe shapes
    const shapes = [];
    for (let i = 0; i < 16; i++) {
      const m = new THREE.Mesh(geos[i % geos.length], new THREE.MeshBasicMaterial({
        color: colors[i % 4], wireframe: true, transparent: true, opacity: 0.5
      }));
      m.position.set((Math.random() - 0.5) * 70, (Math.random() - 0.5) * 40, (Math.random() - 0.5) * 30 - 8);
      m.userData = {
        rx: (Math.random() - 0.5) * 0.012, ry: (Math.random() - 0.5) * 0.012,
        phase: Math.random() * 6, speed: 0.3 + Math.random() * 0.5
      };
      scene.add(m);
      shapes.push(m);
    }

    // particle field
    const N = 500;
    const pos = new Float32Array(N * 3);
    for (let i = 0; i < N * 3; i++) pos[i] = (Math.random() - 0.5) * 120;
    const pg = new THREE.BufferGeometry();
    pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const points = new THREE.Points(pg, new THREE.PointsMaterial({
      color: 0x7fdcff, size: 0.18, transparent: true, opacity: 0.8
    }));
    scene.add(points);

    // big hero object (login page only)
    let knot = null;
    if (hero) {
      knot = new THREE.Mesh(new THREE.TorusKnotGeometry(5, 1.4, 120, 16), new THREE.MeshBasicMaterial({
        color: 0x00e5ff, wireframe: true, transparent: true, opacity: 0.55
      }));
      knot.position.set(-17, 0, 0);
      scene.add(knot);
    }

    function resize() {
      renderer.setSize(window.innerWidth, window.innerHeight, false);
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
    }
    resize();
    window.addEventListener("resize", resize);

    let mx = 0, my = 0;
    window.addEventListener("mousemove", e => {
      mx = e.clientX / window.innerWidth - 0.5;
      my = e.clientY / window.innerHeight - 0.5;
    });

    function animate(t) {
      t *= 0.001;
      shapes.forEach(m => {
        m.rotation.x += m.userData.rx;
        m.rotation.y += m.userData.ry;
        m.position.y += Math.sin(t * m.userData.speed + m.userData.phase) * 0.012;
      });
      points.rotation.y += 0.0006;
      if (knot) { knot.rotation.x += 0.006; knot.rotation.y += 0.009; }
      camera.position.x += (mx * 10 - camera.position.x) * 0.03;
      camera.position.y += (-my * 6 - camera.position.y) * 0.03;
      camera.lookAt(0, 0, 0);
      renderer.render(scene, camera);
      requestAnimationFrame(animate);
    }
    requestAnimationFrame(animate);
  } catch (e) {
    console.log("3D background not available:", e);
  }
}

/* ---------- 3D tilt on cards with class "tilt" ---------- */
let tiltEl = null;
document.addEventListener("mousemove", e => {
  const el = e.target.closest ? e.target.closest(".tilt") : null;
  if (tiltEl && tiltEl !== el) tiltEl.style.transform = "";
  tiltEl = el;
  if (!el) return;
  const r = el.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width - 0.5;
  const y = (e.clientY - r.top) / r.height - 0.5;
  el.style.transform =
    "perspective(900px) rotateY(" + (x * 12) + "deg) rotateX(" + (-y * 12) + "deg) translateZ(10px)";
});

addLayers();
init3D(window.HERO3D === true);


/* ---------- marks analysis display (teacher + admin pages) ---------- */
function analysisHTML(a) {
  const fmt = v => (Number.isInteger(v) ? v : +v.toFixed(2));
  const note = a.missing
    ? '<div class="note">' + a.missing + " student" + (a.missing > 1 ? "s have" : " has") +
      " no marks yet and " + (a.missing > 1 ? "are" : "is") + " not counted.</div>"
    : "";
  if (!a.count) return note + '<div class="note">No marks entered yet for this selection.</div>';

  const lim = a.limit ? " / " + a.limit : "";
  const names = v => a.ranks.filter(r => r.mark === v).map(r => esc(r.name)).join(", ");
  const medal = r => (r === 1 ? "🥇 " : r === 2 ? "🥈 " : r === 3 ? "🥉 " : "");

  const rows = a.ranks.map(r =>
    '<tr class="rank' + r.rank + '"><td>' + medal(r.rank) + r.rank + "</td><td>" + esc(r.roll) +
    "</td><td>" + esc(r.name) + "</td><td>" + fmt(r.mark) + lim + "</td></tr>").join("");

  const same = a.same.length
    ? a.same.map(g => '<div class="same-row"><b>' + fmt(g.mark) + lim + "</b> " +
        g.students.map(n => esc(n)).join(", ") + "</div>").join("")
    : '<div class="note">No two students have the same marks.</div>';

  return note +
    '<div class="cards">' +
    '<div class="card c-green"><span>⬆ Highest</span><b>' + fmt(a.max) + lim + '</b><div class="advice">' + names(a.max) + "</div></div>" +
    '<div class="card c-red"><span>⬇ Lowest</span><b>' + fmt(a.min) + lim + '</b><div class="advice">' + names(a.min) + "</div></div>" +
    '<div class="card c-blue"><span>📊 Average</span><b>' + fmt(a.avg) + lim + '</b><div class="advice">of ' + a.count + " students</div></div>" +
    "</div>" +
    '<div class="sep">&gt; MERIT LIST</div>' +
    '<div class="note">Students with equal marks share the same rank (for example 1, 2, 2, 4).</div>' +
    '<div class="table-wrap"><table><thead><tr><th>Rank</th><th>Roll</th><th>Name</th><th>Marks</th></tr></thead><tbody>' +
    rows + "</tbody></table></div>" +
    '<div class="sep">&gt; STUDENTS WITH THE SAME MARKS</div>' + same;
}
