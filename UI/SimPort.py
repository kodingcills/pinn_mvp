"""
sim_view.py — embeds a live MuJoCo render into a Tkinter widget.

Add to your pace app:
    from sim_view import SimView

    self.sim_view = SimView(self.root, "path/to/model.xml")
    self.sim_view.pack(side="left", fill="both", expand=True)

The sidebar doesn't change at all, it's already parented directly to
root, completely independent of whatever ends up in the main area.
"""

import tkinter as tk
import mujoco
from PIL import Image, ImageTk
import pegsim


class SimView(tk.Label):
    """
    A tk.Label that continuously shows the current frame of a running
    MuJoCo simulation. Subclassing Label (not Frame) because all we
    actually need is one thing that displays an image, same reasoning
    as subclassing Frame for DragDropSidebar, pick the base class that
    already does most of the job you need.
    """

    def __init__(self, parent, model_path, width=400, height=300):
        super().__init__(parent, bg="black")

        self.model = mujoco.MjModel.from_xml_path(pegsim)
        self.data = mujoco.MjData(self.model)
        self.renderer = mujoco.Renderer(self.model, height=height, width=width)

        self._running = True
        self._update_frame()

    def _update_frame(self):
        if not self._running:
            return

        # step the physics forward, then render the current state
        mujoco.mj_step(self.model, self.data)
        self.renderer.update_scene(self.data)
        arr = self.renderer.render()  # numpy (height, width, 3) uint8 array

        img = Image.fromarray(arr)
        photo = ImageTk.PhotoImage(img)

        self.config(image=photo)
        self.image = photo  # keep a reference, or Tkinter garbage-collects it and the view goes blank

        # self.after(), not root.after(), works because SimView IS a
        # tk.Widget (via tk.Label), and every widget has its own
        # .after() that's just a convenience wrapper around the same
        # underlying Tcl event loop root uses. Using self here also
        # means this call is naturally scoped to this widget instance.
        self.after(33, self._update_frame)  # ~30fps

    def stop(self):
        """Call this before destroying the window, or the after() loop
        keeps trying to render into a widget that no longer exists."""
        self._running = False