# -*- coding: utf-8 -*-
"""
preview_view.py — 3D 预览的 tkinter Canvas 控件（软件渲染，零新增依赖）。

交互对齐参考的在线 3MF 查看器：
  左键拖拽 = 旋转   滚轮 = 缩放   右键/中键拖拽 = 平移   双击 = Fit
  工具栏开关：自动旋转 / 线框 / 网格 / 坐标轴（由 GUI 调 set_*）

渲染用画家算法：三角形按相机空间深度排序后逐个 create_polygon，
面法线做 Lambert 明暗；超大网格在拖拽 / 自动旋转时自动降质，保证交互不卡。
"""
from __future__ import annotations

import tkinter as tk

import preview3d as p3
import ui_theme as theme

# 辅助元素的颜色集中在这里，不散落到绘制代码里（与 ui_theme 令牌同一色系）。
GRID = "#333b47"
AXIS_X = theme.ERR        # 红
AXIS_Y = theme.OK         # 绿
AXIS_Z = "#7fa8d8"        # 蓝（3D 查看器惯例的 XYZ 三色）
AXIS_TEXT = theme.MUTED
WIRE = theme.ACCENT
EMPTY_HINT = theme.DIM


class PreviewView(tk.Frame):
    """一块自带交互的 3D 视口；``set_mesh()`` 之后即可渲染。"""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=theme.LOG_BG, highlightthickness=1,
                         highlightbackground=theme.LINE, **kw)
        self.canvas = tk.Canvas(self, bg=theme.LOG_BG, highlightthickness=0,
                                takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        self.mesh = None
        self.cam = p3.Camera()
        self.error_text = None
        self.message_text = None
        self._drag = None
        self._pending = False
        self._pending_interactive = False
        self._spin_job = None
        self._bind_events()

    # ------------------------------------------------------------ 对外 API
    def set_mesh(self, mesh):
        self.mesh = mesh
        self.error_text = None
        self.message_text = None
        self.cam = p3.fit_camera(self.cam)
        self.render()

    def show_error(self, message):
        self.error_text = message
        self.message_text = None
        self.mesh = None
        self.render()

    def show_message(self, message):
        """中性提示（解析中…）；不把视口染成错误色。"""
        self.error_text = None
        self.message_text = message
        self.mesh = None
        self.render()

    def fit(self):
        self.cam = p3.fit_camera(self.cam)
        self.render()

    def set_wireframe(self, on):
        self.cam.wireframe = bool(on)
        self.render()

    def set_grid(self, on):
        self.cam.grid = bool(on)
        self.render()

    def set_axes(self, on):
        self.cam.axes = bool(on)
        self.render()

    def set_backface_cull(self, on):
        self.cam.backface_cull = bool(on)
        self.render()

    def set_spin(self, on):
        self.cam.spin = bool(on)
        if self._spin_job is not None:
            try:
                self.after_cancel(self._spin_job)
            except Exception:
                pass
            self._spin_job = None
        if self.cam.spin:
            self._spin_tick()

    def destroy(self):
        if self._spin_job is not None:
            try:
                self.after_cancel(self._spin_job)
            except Exception:
                pass
        super().destroy()

    # ------------------------------------------------------------ 交互
    def _bind_events(self):
        c = self.canvas
        c.bind("<ButtonPress-1>", lambda e: self._press(e, "rotate"))
        c.bind("<ButtonPress-2>", lambda e: self._press(e, "pan"))
        c.bind("<ButtonPress-3>", lambda e: self._press(e, "pan"))
        for btn in ("1", "2", "3"):
            c.bind("<B%s-Motion>" % btn, self._motion)
            c.bind("<ButtonRelease-%s>" % btn, self._release)
        c.bind("<MouseWheel>", self._wheel)
        c.bind("<Double-Button-1>", lambda _e: self.fit())
        c.bind("<Configure>", lambda _e: self.render())

    def _press(self, event, mode):
        self._drag = (mode, event.x, event.y)
        try:
            self.canvas.focus_set()
        except Exception:
            pass

    def _motion(self, event):
        if not self._drag or self.mesh is None:
            return
        mode, lx, ly = self._drag
        dx, dy = event.x - lx, event.y - ly
        self._drag = (mode, event.x, event.y)
        if mode == "rotate":
            self.cam.yaw += dx * 0.008
            self.cam.pitch = max(-1.45, min(1.45, self.cam.pitch + dy * 0.008))
        else:
            self.cam.pan_x += dx
            self.cam.pan_y += dy
        self._schedule(interactive=True)

    def _release(self, _event):
        if not self._drag:
            return
        self._drag = None
        self._schedule(interactive=False)

    def _wheel(self, event):
        if self.mesh is None:
            return
        step = 1.12 if event.delta > 0 else 1.0 / 1.12
        self.cam.zoom = max(0.15, min(25.0, self.cam.zoom * step))
        self._schedule(interactive=True)

    def _schedule(self, interactive):
        """把连续的 motion / wheel 合并成一帧，避免事件堆积。"""
        self._pending_interactive = interactive or self._pending_interactive
        if self._pending:
            return
        self._pending = True
        self.after_idle(self._render_pending)

    def _render_pending(self):
        self._pending = False
        interactive = self._pending_interactive
        self._pending_interactive = False
        self.render(interactive=interactive)

    def _spin_tick(self):
        if not self.cam.spin or self.mesh is None:
            return
        self.cam.yaw += 0.014
        self.render(interactive=True)
        self._spin_job = self.after(45, self._spin_tick)

    # ------------------------------------------------------------ 渲染
    def render(self, interactive=False):
        c = self.canvas
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 40 or h < 40:
            return
        if self.error_text:
            self._draw_message(w, h, self.error_text, theme.ERR)
            return
        if self.message_text:
            self._draw_message(w, h, self.message_text, theme.MUTED)
            return
        if self.mesh is None:
            self._draw_empty(w, h)
            return

        limit = p3.DRAG_RENDER_FACES if interactive else p3.MAX_RENDER_FACES
        if self.cam.wireframe:
            limit = min(limit, 4000)      # 线框下每条边都是独立 item
        mesh = self.mesh.subset(limit)
        cam = self.cam
        cpts = p3.camera_points(mesh.vertices, cam, mesh.center)
        spts = p3.project(cpts, cam, mesh, w, h)

        if cam.grid:
            self._draw_grid(mesh, w, h)
        if cam.wireframe:
            self._draw_wireframe(mesh, spts)
        else:
            self._draw_faces(mesh, cpts, spts, w, h)
        if cam.axes:
            self._draw_axes(mesh, w, h)

    def _draw_faces(self, mesh, cpts, spts, w, h):
        c = self.canvas
        for _z, fi, n in p3.sort_faces(cpts, mesh.faces, self.cam.backface_cull):
            f = mesh.faces[fi]
            p0, p1, p2 = spts[f[0]], spts[f[1]], spts[f[2]]
            xs = (p0[0], p1[0], p2[0])
            ys = (p0[1], p1[1], p2[1])
            if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
                continue                       # 整面在屏幕外，跳过
            col = p3.hex_color(p3.shade(n, mesh.face_colors[fi]))
            # outline 用同色补上相邻三角形之间的 1px 缝（Tk 无抗锯齿）
            c.create_polygon(p0[0], p0[1], p1[0], p1[1], p2[0], p2[1],
                             fill=col, outline=col, width=1)

    def _draw_wireframe(self, mesh, spts):
        c = self.canvas
        for a, b in p3.wireframe_edges(mesh.faces):
            pa, pb = spts[a], spts[b]
            c.create_line(pa[0], pa[1], pb[0], pb[1], fill=WIRE, width=1)

    def _project_segments(self, segments, mesh, w, h):
        """把若干 3D 线段一次性投影，返回 [(p0, p1), ...] 屏幕坐标。"""
        flat = [p for seg in segments for p in seg[:2]]
        cpts = p3.camera_points(flat, self.cam, mesh.center)
        spts = p3.project(cpts, self.cam, mesh, w, h)
        return [(spts[i], spts[i + 1]) for i in range(0, len(spts), 2)]

    def _draw_grid(self, mesh, w, h):
        ext = mesh.radius * 1.6
        cx, cy = mesh.center[0], mesh.center[1]
        z0 = mesh.bounds_min[2]
        segs = []
        for i in range(-5, 6):
            t = ext * i / 5.0
            segs.append(((cx + t, cy - ext, z0), (cx + t, cy + ext, z0)))
            segs.append(((cx - ext, cy + t, z0), (cx + ext, cy + t, z0)))
        for p0, p1 in self._project_segments(segs, mesh, w, h):
            self.canvas.create_line(p0[0], p0[1], p1[0], p1[1],
                                    fill=GRID, width=1)

    def _draw_axes(self, mesh, w, h):
        # 从包围盒最小角（模型原点 / 平台面）伸出，而不是穿过模型中心，
        # 与地面网格同一基准，也不会把坐标轴压在模型正中间挡住看形状。
        length = mesh.radius * 1.6        # 略长于模型，让三色轴伸出模型外看得见
        o = (mesh.bounds_min[0], mesh.bounds_min[1], mesh.bounds_min[2])
        axes = [
            ((o[0] + length, o[1], o[2]), AXIS_X, "X"),
            ((o[0], o[1] + length, o[2]), AXIS_Y, "Y"),
            ((o[0], o[1], o[2] + length), AXIS_Z, "Z"),
        ]
        segs = [(o, tip) for tip, _col, _label in axes]
        projected = self._project_segments(segs, mesh, w, h)
        for (p0, p1), (_tip, col, label) in zip(projected, axes):
            self.canvas.create_line(p0[0], p0[1], p1[0], p1[1],
                                    fill=col, width=2)
            self.canvas.create_text(p1[0] + 9, p1[1], text=label, fill=col,
                                    font=theme.mono(9, "bold"))

    def _draw_empty(self, w, h):
        self._draw_message(
            w, h,
            "打开 .glb / .stl / .3mf 开始预览\n"
            "左键旋转 · 滚轮缩放 · 右键平移 · 双击适配",
            EMPTY_HINT)

    def _draw_message(self, w, h, text, color):
        c = self.canvas
        # 一个等距立方体线框，呼应「3D」而不是空白一块
        s = min(w, h) * 0.11
        cx, cy = w / 2.0, h / 2.0 - min(70, h * 0.14)
        ww = s * 0.866
        p = [(cx, cy - s), (cx + ww, cy - s / 2), (cx + ww, cy + s / 2),
             (cx, cy + s), (cx - ww, cy + s / 2), (cx - ww, cy - s / 2)]
        c.create_line(p + [p[0]], fill=theme.LINE, width=1)
        for i in (0, 2, 4):
            c.create_line(cx, cy, p[i][0], p[i][1], fill=theme.LINE, width=1)
        c.create_text(w / 2.0, h / 2.0 + min(30, h * 0.06), text=text,
                      fill=color, font=theme.body(10), justify="center")
