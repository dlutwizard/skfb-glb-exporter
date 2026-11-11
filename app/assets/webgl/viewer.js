import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { ThreeMFLoader } from 'three/addons/loaders/3MFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const params = new URLSearchParams(location.search);
const fileName = params.get('name') || 'model';
const ext = (fileName.split('.').pop() || '').toLowerCase();
const stage = document.getElementById('stage');
const loading = document.getElementById('loading');
const loadingTitle = document.getElementById('loading-title');
const loadingDetail = document.getElementById('loading-detail');
const progressBar = document.getElementById('progress-bar');
const errorBox = document.getElementById('error');
const errorMessage = document.getElementById('error-message');
const modelName = document.getElementById('model-name');
const statTris = document.getElementById('stat-tris');
const statVerts = document.getElementById('stat-verts');
const statSize = document.getElementById('stat-size');
const statFormat = document.getElementById('stat-format');
const btnFit = document.getElementById('btn-fit');
const btnWire = document.getElementById('btn-wire');
const btnGrid = document.getElementById('btn-grid');
const btnSpin = document.getElementById('btn-spin');
const btnAxes = document.getElementById('btn-axes');

let scene;
let camera;
let renderer;
let controls;
let grid;
let axes;
let modelRoot;
let currentBox = null;
let wireframe = false;

document.title = fileName + ' · 本地 WebGL 预览';
modelName.textContent = fileName;
statFormat.textContent = ext ? ext.toUpperCase() : '—';
loadingTitle.textContent = '正在加载 ' + fileName + '…';

try {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
} catch (err) {
  showError('当前浏览器无法创建 WebGL 上下文。请使用 Chrome / Edge 最新版，并确认硬件加速已开启。');
  throw err;
}

renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x000000, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.98;
stage.appendChild(renderer.domElement);

scene = new THREE.Scene();
const pmremGenerator = new THREE.PMREMGenerator(renderer);
scene.environment = pmremGenerator.fromScene(new RoomEnvironment(), 0.04).texture;
pmremGenerator.dispose();
camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.01, 1e7);

controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.075;
controls.screenSpacePanning = true;
controls.autoRotateSpeed = 1.8;
controls.zoomSpeed = 1.1;

scene.add(new THREE.HemisphereLight(0xe8f2ff, 0x0d1522, 2.2));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.8);
keyLight.position.set(4, 8, 6);
scene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x7fb2ff, 1.15);
fillLight.position.set(-6, 3, -5);
scene.add(fillLight);
const rimLight = new THREE.DirectionalLight(0xffc56e, 0.85);
rimLight.position.set(0, 5, -9);
scene.add(rimLight);

grid = new THREE.GridHelper(1, 24, 0x3f6f9f, 0x223247);
grid.material.transparent = true;
grid.material.opacity = 0.42;
grid.material.depthWrite = false;
scene.add(grid);

axes = new THREE.AxesHelper(1);
axes.visible = false;
scene.add(axes);

modelRoot = new THREE.Group();
scene.add(modelRoot);

btnFit.addEventListener('click', () => fitCamera());
btnWire.addEventListener('click', () => setWireframe(!wireframe));
btnGrid.addEventListener('click', () => {
  grid.visible = !grid.visible;
  btnGrid.classList.toggle('on', grid.visible);
});
btnSpin.addEventListener('click', () => {
  controls.autoRotate = !controls.autoRotate;
  btnSpin.classList.toggle('on', controls.autoRotate);
});
btnAxes.addEventListener('click', () => {
  axes.visible = !axes.visible;
  btnAxes.classList.toggle('on', axes.visible);
});

renderer.domElement.addEventListener('dblclick', () => fitCamera());
window.addEventListener('keydown', (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const key = event.key.toLowerCase();
  if (key === 'f') fitCamera();
  if (key === 'w') setWireframe(!wireframe);
  if (key === 'g') btnGrid.click();
  if (key === 'r') btnSpin.click();
  if (key === 'a') btnAxes.click();
});
window.addEventListener('resize', onResize);

function onResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

function loadModel() {
  setProgress(0, false);
  if (ext === 'glb') {
    new GLTFLoader().load('/model', (gltf) => finishLoad(gltf.scene, false), onProgress, showError);
  } else if (ext === 'stl') {
    new STLLoader().load('/model', (geometry) => {
      geometry.computeVertexNormals();
      const material = new THREE.MeshStandardMaterial({
        color: 0xd8b67a,
        metalness: 0.04,
        roughness: 0.58,
        side: THREE.DoubleSide,
      });
      finishLoad(new THREE.Mesh(geometry, material), true);
    }, onProgress, showError);
  } else if (ext === '3mf') {
    new ThreeMFLoader().load('/model', (object) => finishLoad(object, true), onProgress, showError);
  } else {
    showError('不支持的文件格式：' + ext + '。只支持 .glb / .stl / .3mf。');
  }
}

function finishLoad(object, rotateZUp) {
  while (modelRoot.children.length) {
    modelRoot.remove(modelRoot.children[0]);
  }
  modelRoot.position.set(0, 0, 0);
  modelRoot.rotation.set(0, 0, 0);
  if (rotateZUp) modelRoot.rotation.x = -Math.PI / 2;
  modelRoot.add(object);
  modelRoot.updateMatrixWorld(true);

  const rawBox = new THREE.Box3().setFromObject(modelRoot);
  if (rawBox.isEmpty()) {
    showError('模型里没有可显示的几何体。');
    return;
  }
  const center = rawBox.getCenter(new THREE.Vector3());
  modelRoot.position.x -= center.x;
  modelRoot.position.z -= center.z;
  modelRoot.position.y -= rawBox.min.y;
  modelRoot.updateMatrixWorld(true);
  currentBox = new THREE.Box3().setFromObject(modelRoot);

  const stats = gatherStats(modelRoot);
  const size = currentBox.getSize(new THREE.Vector3());
  statTris.textContent = formatInt(stats.tris);
  statVerts.textContent = formatInt(stats.verts);
  statSize.textContent = formatSize(size);
  fitCamera();
  setWireframe(false);
  hideLoading();
  loadingDetail.textContent = '模型只在本机读取，不上传';
}

function gatherStats(root) {
  let tris = 0;
  let verts = 0;
  root.traverse((object) => {
    if (!object.isMesh || !object.geometry) return;
    const geometry = object.geometry;
    const position = geometry.attributes && geometry.attributes.position;
    if (!position) return;
    verts += position.count;
    tris += geometry.index ? geometry.index.count / 3 : position.count / 3;
  });
  return { tris: Math.round(tris), verts };
}

function fitCamera() {
  if (!currentBox || currentBox.isEmpty()) return;
  const size = currentBox.getSize(new THREE.Vector3());
  const center = currentBox.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 1;
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const distance = (maxDim / (2 * Math.tan(fov / 2))) * 1.42;
  const direction = new THREE.Vector3(1, 0.72, 1).normalize();

  camera.position.copy(center).addScaledVector(direction, distance);
  camera.near = Math.max(maxDim / 5000, 0.000001);
  camera.far = Math.max(maxDim * 200, 100);
  camera.updateProjectionMatrix();

  controls.target.copy(center);
  controls.minDistance = Math.max(maxDim / 1000, 0.000001);
  controls.maxDistance = Math.max(maxDim * 40, 10);
  controls.update();

  grid.scale.setScalar(Math.max(maxDim * 1.8, 0.001));
  axes.scale.setScalar(Math.max(maxDim * 0.25, 0.001));
}

function setWireframe(on) {
  wireframe = !!on;
  btnWire.classList.toggle('on', wireframe);
  modelRoot.traverse((object) => {
    if (!object.isMesh) return;
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    materials.forEach((material) => {
      if (material && 'wireframe' in material) material.wireframe = wireframe;
    });
  });
}

function onProgress(event) {
  if (!event || !event.total) {
    setProgress(8, true);
    return;
  }
  setProgress(Math.min(100, event.loaded / event.total * 100), true);
}

function setProgress(percent, visible) {
  progressBar.style.width = Math.max(2, percent) + '%';
  if (visible) loadingDetail.textContent = percent >= 100 ? '正在生成 GPU 缓冲…' : '正在读取模型…';
}

function hideLoading() {
  loading.classList.add('hidden');
  progressBar.style.width = '100%';
}

function showError(error) {
  const message = error && error.message ? error.message : String(error || '未知错误');
  errorMessage.textContent = message;
  errorBox.classList.remove('hidden');
  loading.classList.add('hidden');
  console.error(error);
}

function formatInt(value) {
  return Number(value || 0).toLocaleString('zh-CN');
}

function formatSize(size) {
  const values = [size.x, size.y, size.z].map((value) => {
    if (!Number.isFinite(value)) return '—';
    if (Math.abs(value) >= 1000) return value.toFixed(0);
    if (Math.abs(value) >= 10) return value.toFixed(1);
    return value.toFixed(2);
  });
  return values.join(' × ');
}

window.__sfdPreview = { fitCamera, setWireframe, getBox: () => currentBox };

animate();
loadModel();