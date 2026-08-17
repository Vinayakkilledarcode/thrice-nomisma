// components/IndicatorWaveform3D.tsx
//
// Renders the Indicator Engine's per-category signal spread as a rotatable
// 3D scene of overlapping planes (Three.js), instead of the flat 2D scatter
// plot. Each category gets its own thin plane: a ribbon running along that
// category's indicator slots (sorted alphabetically for a stable layout),
// with real height variation -- its own hills and troughs -- from each
// indicator's signal score (-1 bearish .. 0 hold .. +1 bullish). Every
// plane is fanned out around a shared vertical center axis at its own
// angle and passes through that same center point, so the planes literally
// cross each other in 3D.
//
// NOTE on category labels: Labels live in a normal HTML legend under the canvas,
// color-coded by each category's average signal, with a numbered swatch so you 
// can match a legend entry to its column (columns are laid out left-to-right 
// in the same order as categoryLabels).
//
// NOTE on rotation: besides the original mouse drag-to-orbit (OrbitControls),
// the view also responds to the keyboard -- arrow keys / WASD orbit
// around the terrain, Q/E or +/- zoom in and out, and R resets to the
// isometric preset. Click the canvas once to focus it, then drive it purely
// from the keypad if that's easier than the mouse. Camera preset buttons
// (Top / Front / Isometric / Auto-rotate) are layered over the canvas for 
// one-click clean angles.
//
// NOTE on reading intersections: every vertex on the terrain is a small hoverable 
// marker dot. Hovering (mouse) or moving a "scan" cursor with the keyboard's bracket 
// keys ([ and ]) lights up that vertex and pops an HTML tooltip showing exactly 
// which indicator it is -- name, category, slot, numeric score, and signal label.
//
// Requires the `three` package: npm install three
// (and, if you're on TypeScript with strict module resolution and don't
// already have bundled types for it, `npm install -D @types/three`).
import React, { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

export interface WaveformPoint {
  name: string;
  category: string;
  categoryIndex: number;
  slotIndex: number;
  score: number; // -1 (bearish) .. 0 (hold) .. 1 (bullish)
  signal: string;
}

interface Props {
  points: WaveformPoint[];
  categoryLabels: string[]; // display labels, in categoryIndex order
  maxSlots: number;         // grid depth = the largest indicator count in any one category
  height?: number;          // canvas height in px
  theme?: 'light' | 'dark'; // when 'dark', the terrain renders monochrome white and the reference grid goes black instead of the usual per-category color scheme
}

const AMPLITUDE = 1.6;      // vertical scale of real signal height
const RIPPLE_AMPLITUDE = 0.14;
const RIPPLE_SPEED = 1.0;

const FIT_MULTIPLIER = 0.66; // fraction of the frame the content should span
const computeFitDistance = (fovDegrees: number, maxContentSize: number): number => {
  const fovRad = fovDegrees * (Math.PI / 180);
  const safeSize = Math.max(maxContentSize, 0.001);
  return (safeSize * FIT_MULTIPLIER) / Math.tan(fovRad / 2);
};

// Keyboard orbit/zoom tuning
const KEY_ORBIT_SPEED = 1.6;   // radians/sec for theta/phi
const KEY_ZOOM_SPEED = 3.2;    // units/sec for radius
const MIN_PHI = 0.12;          // keep camera from flipping through the poles
const MAX_PHI = Math.PI - 0.12;

const colorForScore = (score: number): THREE.Color => {
  const c = new THREE.Color();
  const gold = new THREE.Color('#d4af37');
  if (score >= 0) c.lerpColors(gold, new THREE.Color('#10b981'), Math.min(1, score));
  else c.lerpColors(gold, new THREE.Color('#f43f5e'), Math.min(1, -score));
  return c;
};

const hexForScore = (score: number): string => `#${colorForScore(score).getHexString()}`;

export const CATEGORY_PALETTE = [
  '#22d3ee', // cyan      - Trend
  '#a78bfa', // violet    - Momentum
  '#fb923c', // orange    - Volatility
  '#4ade80', // green     - Volume
  '#f472b6', // pink      - Statistical
  '#facc15', // yellow    - Price Action
  '#60a5fa', // blue      - Candlestick
  '#f87171', // red-coral - (extra categories beyond 7)
  '#c084fc', // purple
  '#2dd4bf', // teal
];

const categoryBaseColor = (categoryIndex: number, totalCategories: number): THREE.Color => {
  if (categoryIndex < CATEGORY_PALETTE.length) return new THREE.Color(CATEGORY_PALETTE[categoryIndex]);
  const hue = (categoryIndex / Math.max(1, totalCategories)) % 1;
  return new THREE.Color().setHSL(hue, 0.65, 0.55);
};

export const hexForCategory = (categoryIndex: number, totalCategories: number): string =>
  `#${categoryBaseColor(categoryIndex, totalCategories).getHexString()}`;

const colorForCategoryScore = (categoryIndex: number, totalCategories: number, score: number): THREE.Color => {
  const base = categoryBaseColor(categoryIndex, totalCategories);
  const hsl = { h: 0, s: 0, l: 0 };
  base.getHSL(hsl);
  const clamped = Math.max(-1, Math.min(1, score));
  const l = Math.min(0.82, Math.max(0.22, hsl.l + clamped * 0.24));
  const s = Math.min(1, Math.max(0.18, hsl.s * (0.55 + 0.45 * Math.abs(clamped))));
  return new THREE.Color().setHSL(hsl.h, s, l);
};

type ViewPreset = 'iso' | 'top' | 'front' | 'side';

interface CategoryBreakdown {
  bulls: WaveformPoint[];
  bears: WaveformPoint[];
  holds: WaveformPoint[];
  total: number;
  verdict: 'BULLISH' | 'BEARISH' | 'MIXED' | 'HOLD';
}

interface HoverInfo {
  point: WaveformPoint;
  x: number; // screen px, relative to mount container
  y: number;
  reasoning: string;
  breakdown: CategoryBreakdown;
  pinned: boolean;
}

const buildReasoning = (point: WaveformPoint, breakdown: CategoryBreakdown): string => {
  const side = point.score > 0.001 ? 'bulls' : point.score < -0.001 ? 'bears' : 'holds';
  const sideList = breakdown[side as 'bulls' | 'bears' | 'holds'];
  const companions = sideList.filter(p => p.name !== point.name).map(p => p.name);
  const companionText = companions.length
    ? ` It agrees with ${companions.slice(0, 3).join(', ')}${companions.length > 3 ? ` and ${companions.length - 3} more` : ''} in this category.`
    : ' No other indicator in this category shares its exact read.';
  const sideWord = side === 'bulls' ? 'bullish' : side === 'bears' ? 'bearish' : 'neutral (hold)';
  const shapeWord = side === 'bulls' ? 'rises above the gold zero-plane, adding to the ridge' : side === 'bears' ? 'sinks below the gold zero-plane, adding to the trench' : 'sits flat on the gold zero-plane';
  return `${point.name} reads ${sideWord} (score ${point.score.toFixed(2)}), so it ${shapeWord} for the ${point.category.replace(/_/g, ' ')} column.${companionText} Across all ${breakdown.total} indicators in this category: ${breakdown.bulls.length} bullish, ${breakdown.bears.length} bearish, ${breakdown.holds.length} neutral — net verdict ${breakdown.verdict}.`;
};

export default function IndicatorWaveform3D({ points, categoryLabels, maxSlots, height = 380, theme = 'light' }: Props) {
  const mountRef = useRef<HTMLDivElement>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const dimsRef = useRef({ cols: 2, rows: 2 });
  const [autoRotate, setAutoRotate] = useState(false);
  const [focused, setFocused] = useState(false);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const unpinRef = useRef<() => void>(() => {});
  const containerWidthRef = useRef(700);

  const [isolatedCategory, setIsolatedCategory] = useState<number | null>(null);
  const isolatedCategoryRef = useRef<number | null>(null);
  useEffect(() => { isolatedCategoryRef.current = isolatedCategory; }, [isolatedCategory]);

  const fitSizeRef = useRef(new THREE.Vector3(4, 3, 4));

  const categoryBreakdowns: CategoryBreakdown[] = useMemo(() => {
    return categoryLabels.map((_, i) => {
      const inCat = points.filter(p => p.categoryIndex === i);
      const bulls = inCat.filter(p => p.score > 0.001);
      const bears = inCat.filter(p => p.score < -0.001);
      const holds = inCat.filter(p => p.score >= -0.001 && p.score <= 0.001);
      const total = inCat.length;
      let verdict: CategoryBreakdown['verdict'] = 'HOLD';
      if (total > 0) {
        if (bulls.length > bears.length && bulls.length > holds.length) verdict = 'BULLISH';
        else if (bears.length > bulls.length && bears.length > holds.length) verdict = 'BEARISH';
        else if (bulls.length === bears.length && bulls.length > 0) verdict = 'MIXED';
      }
      return { bulls, bears, holds, total, verdict };
    });
  }, [points, categoryLabels]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    const cols = Math.max(2, categoryLabels.length);
    const rows = Math.max(2, maxSlots);
    dimsRef.current = { cols, rows };
    const isDark = theme === 'dark';

    const sceneBg = isDark ? 0xffffff : 0x000000;
    const terrainVertexColor = (ix: number, score: number): THREE.Color => colorForCategoryScore(ix, cols, score);
    const wireframeColor = (ix: number): THREE.Color | number => categoryBaseColor(ix, cols);

    const heightGrid: number[][] = Array.from({ length: cols }, () => Array(rows).fill(0));
    const pointGrid: (WaveformPoint | null)[][] = Array.from({ length: cols }, () => Array(rows).fill(null));
    points.forEach(p => {
      if (p.categoryIndex >= 0 && p.categoryIndex < cols && p.slotIndex >= 0 && p.slotIndex < rows) {
        heightGrid[p.categoryIndex][p.slotIndex] = p.score;
        pointGrid[p.categoryIndex][p.slotIndex] = p;
      }
    });

    const width = mount.clientWidth || 700;
    let currentWidth = width;
    containerWidthRef.current = width;
    const scene = new THREE.Scene();

    scene.background = new THREE.Color(sceneBg);

    const maxDim = Math.max(cols, rows);
    const camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 1000);
    cameraRef.current = camera;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.innerHTML = '';
    mount.appendChild(renderer.domElement);
    rendererRef.current = renderer;

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enablePan = true;
    controls.screenSpacePanning = true;
    controls.minDistance = 1.5;
    controls.maxDistance = maxDim * 4;
    controls.target.set(0, 0, 0);
    controls.autoRotateSpeed = 1.2;
    camera.lookAt(controls.target);
    controlsRef.current = controls;

    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.75);
    dirLight.position.set(6, 12, 8);
    scene.add(dirLight);
    const fillLight = new THREE.DirectionalLight(0xd4af37, 0.25);
    fillLight.position.set(-6, 6, -8);
    scene.add(fillLight);

    const half = (rows - 1) / 2;
    const thetaStep = cols > 1 ? Math.PI / cols : 0;
    const planeHalfWidth = 0.34;

    const spineDirFor = (theta: number) => new THREE.Vector3(Math.cos(theta), 0, Math.sin(theta));
    const widthDirFor = (theta: number) => new THREE.Vector3(-Math.sin(theta), 0, Math.cos(theta));
    const planeSpineDirs: THREE.Vector3[] = [];

    const planeGroup = new THREE.Group();
    const planeGeometries: THREE.BufferGeometry[] = [];
    const planeMaterials: THREE.MeshStandardMaterial[] = [];
    const planeWireGeometries: THREE.WireframeGeometry[] = [];
    const planeWireMaterials: THREE.LineBasicMaterial[] = [];
    const planeBaseHeights: Float32Array[] = [];

    for (let ix = 0; ix < cols; ix++) {
      const theta = ix * thetaStep;
      const d = spineDirFor(theta);
      const w = widthDirFor(theta);
      planeSpineDirs.push(d);

      const positions = new Float32Array(rows * 2 * 3);
      const colors = new Float32Array(rows * 2 * 3);
      const baseHeights = new Float32Array(rows);

      for (let iy = 0; iy < rows; iy++) {
        const score = heightGrid[ix][iy];
        baseHeights[iy] = score;
        const spineDist = iy - half;
        const c = terrainVertexColor(ix, score);
        for (let side = 0; side < 2; side++) {
          const vIdx = iy * 2 + side;
          const wOff = side === 0 ? -planeHalfWidth : planeHalfWidth;
          positions[vIdx * 3 + 0] = d.x * spineDist + w.x * wOff;
          positions[vIdx * 3 + 1] = score * AMPLITUDE;
          positions[vIdx * 3 + 2] = d.z * spineDist + w.z * wOff;
          colors[vIdx * 3 + 0] = c.r;
          colors[vIdx * 3 + 1] = c.g;
          colors[vIdx * 3 + 2] = c.b;
        }
      }

      const indices: number[] = [];
      for (let iy = 0; iy < rows - 1; iy++) {
        const a = iy * 2 + 0;
        const b = iy * 2 + 1;
        const c2 = (iy + 1) * 2 + 0;
        const d2 = (iy + 1) * 2 + 1;
        indices.push(a, c2, b, b, c2, d2);
      }

      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
      geometry.setIndex(indices);
      geometry.computeVertexNormals();
      planeGeometries.push(geometry);
      planeBaseHeights.push(baseHeights);

      const material = new THREE.MeshStandardMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
        flatShading: true,
        roughness: 0.5,
        metalness: 0.12,
        transparent: true,
        opacity: 0.72,
      });
      planeMaterials.push(material);
      const mesh = new THREE.Mesh(geometry, material);
      planeGroup.add(mesh);

      const wireGeo = new THREE.WireframeGeometry(geometry);
      const wireMat = new THREE.LineBasicMaterial({
        color: wireframeColor(ix),
        transparent: true,
        opacity: 0.32,
      });
      planeWireGeometries.push(wireGeo);
      planeWireMaterials.push(wireMat);
      planeGroup.add(new THREE.LineSegments(wireGeo, wireMat));
    }
    scene.add(planeGroup);

    const fitBox = new THREE.Box3().setFromObject(planeGroup);
    const fitSize = new THREE.Vector3();
    fitBox.getSize(fitSize);
    fitSize.x = Math.max(fitSize.x, maxDim * 0.5);
    fitSize.y = Math.max(fitSize.y, AMPLITUDE);
    fitSize.z = Math.max(fitSize.z, maxDim * 0.5);
    fitSizeRef.current = fitSize;

    const fitDistance = computeFitDistance(camera.fov, Math.max(fitSize.x, fitSize.y, fitSize.z));
    const isoDir = new THREE.Vector3(0.95, 0.95, 1.15).normalize();
    camera.position.copy(isoDir).multiplyScalar(fitDistance);
    camera.lookAt(controls.target);
    controls.minDistance = Math.max(1.5, fitDistance * 0.3);
    controls.maxDistance = fitDistance * 2.4;
    controls.update();

    const spineGeo = new THREE.BufferGeometry().setFromPoints([
      new THREE.Vector3(0, -AMPLITUDE - 0.4, 0),
      new THREE.Vector3(0, AMPLITUDE + 0.4, 0),
    ]);
    const spineMat = new THREE.LineBasicMaterial({ color: 0xd4af37, transparent: true, opacity: 0.5 });
    const centerSpine = new THREE.Line(spineGeo, spineMat);
    scene.add(centerSpine);

    const floorRadius = half + 1.6;
    const grid = new THREE.PolarGridHelper(floorRadius, Math.max(cols * 2, 8), 6, 48, isDark ? 0x000000 : 0xffffff, isDark ? 0x333333 : 0x555a66);
    grid.position.y = -AMPLITUDE - 0.4;
    (Array.isArray(grid.material) ? grid.material : [grid.material]).forEach(m => {
      m.transparent = true;
      m.opacity = 0.35;
    });
    scene.add(grid);

    const holdPlaneGeo = new THREE.CircleGeometry(floorRadius, 48);
    const holdPlaneMat = new THREE.MeshBasicMaterial({ color: isDark ? 0x000000 : 0xffffff, transparent: true, opacity: isDark ? 0.045 : 0.07, side: THREE.DoubleSide });
    const holdPlane = new THREE.Mesh(holdPlaneGeo, holdPlaneMat);
    holdPlane.rotation.x = -Math.PI / 2;
    scene.add(holdPlane);

    const markerIndexToPoint = new Map<number, WaveformPoint>();
    const markerPositions: number[] = [];
    const markerColorsArr: number[] = [];
    let markerCursor = 0;
    for (let iy = 0; iy < rows; iy++) {
      for (let ix = 0; ix < cols; ix++) {
        const p = pointGrid[ix][iy];
        if (!p) continue;
        const d = planeSpineDirs[ix];
        const spineDist = iy - half;
        markerPositions.push(d.x * spineDist, p.score * AMPLITUDE, d.z * spineDist);
        const mc = colorForCategoryScore(ix, cols, p.score);
        markerColorsArr.push(mc.r, mc.g, mc.b);
        markerIndexToPoint.set(markerCursor, p);
        markerCursor++;
      }
    }
    const markerGeo = new THREE.BufferGeometry();
    markerGeo.setAttribute('position', new THREE.Float32BufferAttribute(markerPositions, 3));
    markerGeo.setAttribute('color', new THREE.Float32BufferAttribute(markerColorsArr, 3));
    const markerMat = new THREE.PointsMaterial({
      vertexColors: true,
      size: 0.12,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.95,
    });
    const markers = new THREE.Points(markerGeo, markerMat);
    scene.add(markers);

    const highlightGeo = new THREE.RingGeometry(0.09, 0.14, 20);
    const highlightMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, side: THREE.DoubleSide });
    const highlightRing = new THREE.Mesh(highlightGeo, highlightMat);
    scene.add(highlightRing);

    const raycaster = new THREE.Raycaster();
    raycaster.params.Points = { threshold: 0.16 };
    const ndc = new THREE.Vector2();
    let hoveredMarkerIndex: number | null = null;
    let pinnedIndex: number | null = null;
    let scanIndex = 0;

    const applyHover = (markerIdx: number | null, pinned = false) => {
      hoveredMarkerIndex = markerIdx;
      if (markerIdx === null) {
        if (pinnedIndex === null) {
          highlightMat.opacity = 0;
          setHover(null);
        }
        renderer.domElement.style.cursor = focused ? 'default' : 'grab';
        return;
      }
      const p = markerIndexToPoint.get(markerIdx);
      if (!p) return;
      const vx = markerPositions[markerIdx * 3 + 0];
      const vy = markerPositions[markerIdx * 3 + 1];
      const vz = markerPositions[markerIdx * 3 + 2];
      highlightRing.position.set(vx, vy, vz);
      highlightRing.quaternion.copy(camera.quaternion);
      highlightMat.opacity = 0.95;
      highlightMat.color.set(colorForCategoryScore(p.categoryIndex, cols, p.score));
      renderer.domElement.style.cursor = 'pointer';

      const vec = new THREE.Vector3(vx, vy, vz).project(camera);
      const sx = (vec.x * 0.5 + 0.5) * currentWidth;
      const sy = (-vec.y * 0.5 + 0.5) * height;
      const breakdown = categoryBreakdowns[p.categoryIndex] || { bulls: [], bears: [], holds: [], total: 0, verdict: 'HOLD' as const };
      setHover({ point: p, x: sx, y: sy, reasoning: buildReasoning(p, breakdown), breakdown, pinned: pinned || pinnedIndex === markerIdx });
    };

    const onPointerMove = (ev: PointerEvent) => {
      if (pinnedIndex !== null) return;
      const rect = renderer.domElement.getBoundingClientRect();
      ndc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      ndc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(ndc, camera);
      const hits = raycaster.intersectObject(markers, false);
      applyHover(hits.length ? (hits[0].index ?? null) : null);
    };
    const onPointerLeave = () => {
      if (pinnedIndex === null) applyHover(null);
    };
    const onClick = (ev: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      ndc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      ndc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(ndc, camera);
      const hits = raycaster.intersectObject(markers, false);
      const hitIdx = hits.length ? (hits[0].index ?? null) : null;
      if (hitIdx === null) {
        pinnedIndex = null;
        applyHover(null);
      } else if (pinnedIndex === hitIdx) {
        pinnedIndex = null;
        applyHover(hitIdx);
      } else {
        pinnedIndex = hitIdx;
        applyHover(hitIdx, true);
      }
    };
    renderer.domElement.addEventListener('pointermove', onPointerMove);
    renderer.domElement.addEventListener('pointerleave', onPointerLeave);
    renderer.domElement.addEventListener('click', onClick);
    unpinRef.current = () => {
      pinnedIndex = null;
      applyHover(null);
    };

    const keysDown = new Set<string>();
    const spherical = new THREE.Spherical();

    const syncSphericalFromCamera = () => {
      const offset = new THREE.Vector3().copy(camera.position).sub(controls.target);
      spherical.setFromVector3(offset);
    };
    syncSphericalFromCamera();

    const onKeyDown = (ev: KeyboardEvent) => {
      if (!mount.contains(document.activeElement) && document.activeElement !== renderer.domElement) return;
      const key = ev.key;
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' '].includes(key)) ev.preventDefault();
      keysDown.add(key.toLowerCase());

      if (key === 'r' || key === 'R') {
        camera.position.copy(isoDir).multiplyScalar(fitDistance);
        controls.target.set(0, 0, 0);
        camera.lookAt(controls.target);
        syncSphericalFromCamera();
      }
      if (key === '[' || key === ']') {
        const total = markerIndexToPoint.size;
        if (total > 0) {
          scanIndex = ((scanIndex + (key === ']' ? 1 : -1)) % total + total) % total;
          pinnedIndex = scanIndex;
          applyHover(scanIndex, true);
        }
      }
      if (key === 'Escape') {
        pinnedIndex = null;
        applyHover(null);
      }
    };
    const onKeyUp = (ev: KeyboardEvent) => keysDown.delete(ev.key.toLowerCase());
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);

    const applyKeyboardOrbit = (dt: number) => {
      if (keysDown.size === 0) return;
      let dTheta = 0;
      let dPhi = 0;
      let dRadius = 0;
      if (keysDown.has('arrowleft') || keysDown.has('a')) dTheta -= KEY_ORBIT_SPEED * dt;
      if (keysDown.has('arrowright') || keysDown.has('d')) dTheta += KEY_ORBIT_SPEED * dt;
      if (keysDown.has('arrowup') || keysDown.has('w')) dPhi -= KEY_ORBIT_SPEED * dt;
      if (keysDown.has('arrowdown') || keysDown.has('s')) dPhi += KEY_ORBIT_SPEED * dt;
      if (keysDown.has('q') || keysDown.has('-') || keysDown.has('_')) dRadius += KEY_ZOOM_SPEED * dt;
      if (keysDown.has('e') || keysDown.has('=') || keysDown.has('+')) dRadius -= KEY_ZOOM_SPEED * dt;
      if (!dTheta && !dPhi && !dRadius) return;

      syncSphericalFromCamera();
      spherical.theta += dTheta;
      spherical.phi = Math.max(MIN_PHI, Math.min(MAX_PHI, spherical.phi + dPhi));
      spherical.radius = Math.max(controls.minDistance, Math.min(controls.maxDistance, spherical.radius + dRadius));
      const offset = new THREE.Vector3().setFromSpherical(spherical);
      camera.position.copy(controls.target).add(offset);
      camera.lookAt(controls.target);
    };

    const clock = new THREE.Clock();
    let rafId = 0;
    let disposed = false;
    let lastT = clock.getElapsedTime();
    let lastIsolated: number | null | undefined = undefined;

    const animate = () => {
      if (disposed) return;
      rafId = requestAnimationFrame(animate);
      const t = clock.getElapsedTime();
      const dt = t - lastT;
      lastT = t;

      const isolatedNow = isolatedCategoryRef.current;
      if (isolatedNow !== lastIsolated) {
        lastIsolated = isolatedNow;
        for (let ix = 0; ix < cols; ix++) {
          const dimmed = isolatedNow !== null && ix !== isolatedNow;
          planeMaterials[ix].opacity = dimmed ? 0.04 : 0.72;
          planeWireMaterials[ix].opacity = dimmed ? 0.03 : 0.32;
        }
        const colorAttr = markerGeo.attributes.color as THREE.BufferAttribute;
        for (let idx = 0; idx < markerCursor; idx++) {
          const p = markerIndexToPoint.get(idx);
          const dimmed = isolatedNow !== null && (!p || p.categoryIndex !== isolatedNow);
          const factor = dimmed ? 0.03 : 1;
          colorAttr.setXYZ(
            idx,
            markerColorsArr[idx * 3 + 0] * factor,
            markerColorsArr[idx * 3 + 1] * factor,
            markerColorsArr[idx * 3 + 2] * factor,
          );
        }
        colorAttr.needsUpdate = true;
      }

      for (let ix = 0; ix < cols; ix++) {
        const posAttr = planeGeometries[ix].attributes.position as THREE.BufferAttribute;
        const baseHeights = planeBaseHeights[ix];
        for (let iy = 0; iy < rows; iy++) {
          const base = baseHeights[iy] * AMPLITUDE;
          const ripple = RIPPLE_AMPLITUDE * Math.sin(t * RIPPLE_SPEED + ix * 0.55 + iy * 0.42);
          const y = base + ripple;
          posAttr.setY(iy * 2 + 0, y);
          posAttr.setY(iy * 2 + 1, y);
        }
        posAttr.needsUpdate = true;
        planeGeometries[ix].computeVertexNormals();
      }

      applyKeyboardOrbit(dt);
      if (hoveredMarkerIndex !== null) applyHover(hoveredMarkerIndex);
      if (highlightMat.opacity > 0) highlightRing.quaternion.copy(camera.quaternion);

      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const resizeObserver = new ResizeObserver(() => {
      const w = mount.clientWidth || width;
      const h = mount.clientHeight || height;
      currentWidth = w;
      containerWidthRef.current = w;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      renderer.setViewport(0, 0, w, h);
    });
    resizeObserver.observe(mount);

    return () => {
      disposed = true;
      cancelAnimationFrame(rafId);
      resizeObserver.disconnect();
      rendererRef.current = null;
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      renderer.domElement.removeEventListener('pointerleave', onPointerLeave);
      renderer.domElement.removeEventListener('click', onClick);
      controls.dispose();
      planeGeometries.forEach(g => g.dispose());
      planeMaterials.forEach(m => m.dispose());
      planeWireGeometries.forEach(g => g.dispose());
      planeWireMaterials.forEach(m => m.dispose());
      spineGeo.dispose();
      spineMat.dispose();
      holdPlaneGeo.dispose();
      holdPlaneMat.dispose();
      markerGeo.dispose();
      markerMat.dispose();
      highlightGeo.dispose();
      highlightMat.dispose();
      renderer.dispose();
      if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
      cameraRef.current = null;
      controlsRef.current = null;
    };
  }, [JSON.stringify(points.map(p => `${p.categoryIndex}:${p.slotIndex}:${p.score.toFixed(2)}`)), categoryLabels.join('|'), maxSlots, theme, height]);

  useEffect(() => {
    if (controlsRef.current) controlsRef.current.autoRotate = autoRotate;
  }, [autoRotate]);

  const setView = (preset: ViewPreset) => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;
    const size = fitSizeRef.current;
    const fitDistance = computeFitDistance(camera.fov, Math.max(size.x, size.y, size.z));
    controls.target.set(0, 0, 0);
    switch (preset) {
      case 'iso':
        camera.position.set(fitDistance * 0.72, fitDistance * 0.72, fitDistance * 0.86);
        break;
      case 'top':
        camera.position.set(0.001, fitDistance * 1.15, 0.001);
        break;
      case 'front':
        camera.position.set(0, fitDistance * 0.2, fitDistance * 1.15);
        break;
      case 'side':
        camera.position.set(fitDistance * 1.15, fitDistance * 0.2, 0);
        break;
    }
    camera.lookAt(controls.target);
    controls.update();
  };

  const themeBg = theme === 'dark' ? '#ffffff' : '#000000';

  return (
    <div style={{ width: '100%' }}>
      <div style={{ position: 'relative', width: '100%', height: height, flexShrink: 0 }}>
        <div
          ref={mountRef}
          tabIndex={0}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          style={{
            width: '100%',
            height: '100%',
            background: themeBg,
            cursor: 'grab',
            outline: focused ? '1px solid rgba(212,175,55,0.55)' : '1px solid transparent',
            outlineOffset: -1,
            borderRadius: 4,
            transition: 'outline-color 0.15s ease',
          }}
        />

        {/* Hover / keyboard-scan tooltip */}
        {hover && (() => {
          const catColor = hexForCategory(hover.point.categoryIndex, categoryLabels.length);
          const verdictColor = hover.breakdown.verdict === 'BULLISH' ? '#4ade80' : hover.breakdown.verdict === 'BEARISH' ? '#f87171' : hover.breakdown.verdict === 'MIXED' ? '#facc15' : '#9ca3af';
          const flipLeft = hover.x > containerWidthRef.current * 0.6;
          return (
            <div
              style={{
                position: 'absolute',
                left: flipLeft ? undefined : Math.max(hover.x + 12, 4),
                right: flipLeft ? Math.max(containerWidthRef.current - hover.x + 12, 4) : undefined,
                top: Math.max(hover.y - 14, 4),
                background: 'rgba(8,9,12,0.96)',
                border: `1px solid ${catColor}`,
                borderRadius: 6,
                padding: '8px 10px',
                fontSize: 11,
                fontFamily: 'var(--font-data, monospace)',
                color: 'var(--silver, #c8ccd4)',
                pointerEvents: hover.pinned ? 'auto' : 'none',
                whiteSpace: 'normal',
                width: 260,
                lineHeight: 1.45,
                zIndex: 5,
                boxShadow: '0 4px 18px rgba(0,0,0,0.6)',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: catColor, flexShrink: 0, display: 'inline-block' }} />
                  <span style={{ fontWeight: 700, color: '#fff' }}>{hover.point.name}</span>
                </div>
                {hover.pinned && (
                  <button
                    type="button"
                    onClick={() => unpinRef.current()}
                    style={{ background: 'none', border: 'none', color: 'var(--silver-dim, #7a7f8a)', cursor: 'pointer', fontSize: 12, lineHeight: 1, padding: 0 }}
                    aria-label="Close"
                  >
                    ×
                  </button>
                )}
              </div>
              <div style={{ color: catColor, marginTop: 2 }}>{hover.point.category.replace(/_/g, ' ')}</div>
              <div style={{ marginTop: 3 }}>
                score <span style={{ color: hexForScore(hover.point.score), fontWeight: 700 }}>{hover.point.score.toFixed(2)}</span>
                {'  ·  '}
                {hover.point.signal}
                {'  ·  '}
                <span style={{ color: verdictColor, fontWeight: 700 }}>{hover.breakdown.verdict}</span>
              </div>
              <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid rgba(255,255,255,0.08)', color: 'var(--silver, #c8ccd4)' }}>
                {hover.reasoning}
              </div>
              {!hover.pinned && (
                <div style={{ marginTop: 6, fontSize: 9.5, color: 'var(--silver-dim, #7a7f8a)' }}>click to pin</div>
              )}
            </div>
          );
        })()}

        <div
          style={{
            position: 'absolute',
            top: 8,
            right: 8,
            display: 'flex',
            gap: 6,
            flexWrap: 'wrap',
            justifyContent: 'flex-end',
            maxWidth: '60%',
          }}
        >
          {(['iso', 'top', 'front', 'side'] as ViewPreset[]).map(preset => (
            <button
              key={preset}
              type="button"
              onClick={() => setView(preset)}
              style={viewBtnStyle}
            >
              {preset === 'iso' ? 'Isometric' : preset.charAt(0).toUpperCase() + preset.slice(1)}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setAutoRotate(r => !r)}
            style={{ ...viewBtnStyle, background: autoRotate ? 'var(--gold, #d4af37)' : 'rgba(20,22,28,0.7)', color: autoRotate ? '#111' : '#d4af37' }}
          >
            {autoRotate ? 'Stop Rotate' : 'Auto-Rotate'}
          </button>
        </div>

        {/* Keyboard hints inside the viewport wrapper */}
        <div
          style={{
            position: 'absolute',
            bottom: 8,
            left: 8,
            fontSize: 10,
            fontFamily: 'var(--font-data, monospace)',
            color: 'rgba(200,204,212,0.65)',
            background: 'rgba(14,16,20,0.6)',
            border: '1px solid var(--border-subtle, #2a2d35)',
            borderRadius: 4,
            padding: '4px 7px',
            opacity: focused ? 1 : 0.55,
            pointerEvents: 'none',
            transition: 'opacity 0.15s ease',
            maxWidth: '55%',
          }}
        >
          {focused
            ? 'Arrows/WASD: rotate · Q/E or -/+: zoom · [ ]: step through indicators · R: reset'
            : 'Click the plot to enable keyboard rotation'}
        </div>
      </div>

      {/* HTML legend */}
      <div>
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '8px 18px',
            marginTop: 10,
            padding: '8px 10px',
            fontSize: 11,
            fontFamily: 'var(--font-data, monospace)',
            color: 'var(--silver, #c8ccd4)',
            borderTop: '1px solid var(--border-subtle, #2a2d35)',
            alignItems: 'center',
          }}
        >
          {categoryLabels.map((label, i) => {
            const b = categoryBreakdowns[i] || { bulls: [], bears: [], holds: [], total: 0, verdict: 'HOLD' as const };
            const verdictColor = b.verdict === 'BULLISH' ? '#4ade80' : b.verdict === 'BEARISH' ? '#f87171' : b.verdict === 'MIXED' ? '#facc15' : '#9ca3af';
            const isIsolated = isolatedCategory === i;
            const dimmedByOther = isolatedCategory !== null && !isIsolated;
            return (
              <span
                key={label + i}
                onClick={() => setIsIsolatedCategory(prev => (prev === i ? null : i))}
                title={isIsolated ? `Showing only ${label} -- click again to show all categories` : `Click to show only ${label}'s points`}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer',
                  padding: '3px 6px', borderRadius: 3,
                  background: isIsolated ? 'rgba(212,175,55,0.12)' : 'transparent',
                  border: isIsolated ? '1px solid var(--gold, #d4af37)' : '1px solid transparent',
                  opacity: dimmedByOther ? 0.45 : 1,
                  transition: 'opacity 0.15s ease, background 0.15s ease, border 0.15s ease',
                }}
              >
                <span
                  style={{
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    background: hexForCategory(i, categoryLabels.length),
                    display: 'inline-block',
                    flexShrink: 0,
                  }}
                />
                <span style={{ color: 'var(--gold-dim, #a8863f)', fontWeight: 700 }}>{i + 1}.</span>
                <span>{label}</span>
                <span style={{ color: 'var(--silver-dim, #7a7f8a)' }}>
                  ({b.bulls.length}<span style={{ color: '#4ade80' }}>▲</span>
                  {' '}{b.holds.length}<span style={{ color: '#9ca3af' }}>●</span>
                  {' '}{b.bears.length}<span style={{ color: '#f87171' }}>▼</span>)
                </span>
                <span style={{ color: verdictColor, fontWeight: 700, fontSize: 9.5 }}>{b.verdict}</span>
              </span>
            );
          })}
          {isolatedCategory !== null && (
            <button
              type="button"
              onClick={() => setIsIsolatedCategory(null)}
              style={{
                marginLeft: 'auto', fontSize: 9.5, fontFamily: 'var(--font-data, monospace)',
                color: '#111', background: 'var(--gold, #d4af37)', border: 'none',
                borderRadius: 3, padding: '4px 10px', cursor: 'pointer', fontWeight: 700, letterSpacing: '0.03em',
              }}
            >
              SHOW ALL
            </button>
          )}
        </div>

        <div style={{ marginTop: 6, padding: '0 10px', fontSize: 9.5, fontFamily: 'var(--font-data, monospace)', color: 'var(--silver-dim, #7a7f8a)', lineHeight: 1.5 }}>
          {isolatedCategory !== null
            ? `Isolated: ${categoryLabels[isolatedCategory]} only -- click its legend entry again or SHOW ALL to bring the other categories back.`
            : 'Hue = indicator category (see legend above) · Brighter/lighter = stronger bullish, darker = stronger bearish, mid-tone = hold · each category is its own plane, fanned around the gold center spine so planes physically cross where categories disagree · white circular plane = the zero/HOLD reference level · click any vertex to pin its explanation open · click a legend entry to isolate that category.'}
        </div>
      </div>
    </div>
  );
}

const viewBtnStyle: React.CSSProperties = {
  fontSize: 10,
  padding: '4px 8px',
  borderRadius: 4,
  border: '1px solid var(--border-subtle, #2a2d35)',
  background: 'rgba(20,22,28,0.7)',
  color: '#d4af37',
  cursor: 'pointer',
  fontFamily: 'var(--font-data, monospace)',
  letterSpacing: '0.03em',
};