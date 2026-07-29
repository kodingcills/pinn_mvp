"""
pace — the same app, rebuilt in Tkinter (Python's built-in GUI library,
no pip install needed on most systems, it ships with Python).

Run with:
    python3 pace_app_tkinter.py

=====================================================================
WHERE THIS SITS RELATIVE TO WHAT YOU'VE ALREADY SEEN
=====================================================================
Tkinter is RETAINED mode, same family as PyQt: you build widget
OBJECTS once, keep references to them, and mutate them later by
calling methods (like label.config(text="new text")). This is the
opposite of the ImGui version, where nothing persisted and you just
redrew everything from state every frame.

If PyQt felt heavy (signals, layouts, QSS) and ImGui felt unusual
(no objects, draw-every-frame), Tkinter sits in between: still
retained-mode like Qt, but much smaller and plainer, fewer widget
types, no CSS-like styling language, and a layout system (pack/grid)
that's simpler but less powerful than Qt's.

Nobody chooses Tkinter because it looks good, it doesn't, by default.
People choose it because it's already installed, has zero dependencies,
and is fine for quick internal tools. This file leans into styling it
about as far as Tkinter reasonably goes, to hit the same look as the
other three versions, but there's a visible ceiling on how far that
can go compared to Qt or ImGui.
=====================================================================
"""
import tkinter as tk
from tkinter import font as tkfont
from tkinterdnd2 import TkinterDnD, DND_FILES
from Drag import DragDropSidebar
from SimPort import SimView




# ---------------------------------------------------------------
# DESIGN TOKENS — same values as every other version of this app.
# Tkinter takes hex strings directly for colors, no conversion
# needed here (unlike ImGui's 0.0-1.0 float tuples).
# ---------------------------------------------------------------
PAPER = "#F7F5F0"
INK = "#1C1D21"
SLATE = "#4B5468"
AMBER = "#E8862E"

SPACE_XS, SPACE_SM, SPACE_MD = 8, 16, 24
SIDEBAR_WIDTH = 140


class PaceApp:
    """
    Like the PyQt version, this is a class because Tkinter is retained
    mode: something needs to hold onto widget references between the
    moment they're built and the moment an event handler needs to
    change them later. self.streak_label is that reference, the same
    role self.streak_value played in the PyQt file.
    """

    def __init__(self, root):
        self.root = root
        self.streak = 23

        root.title("pace")
        root.geometry("480x300")
        root.resizable(False, False)
        root.configure(bg=PAPER)

        # -------------------------------------------------------
        # FONTS
        # Tkinter, like Qt, wants explicit Font objects rather than
        # CSS-style font-size/font-weight strings. Named here once
        # and reused, same discipline as the QFont objects in the
        # PyQt version.
        # -------------------------------------------------------
        self.font_eyebrow = tkfont.Font(family="Courier New", size=10)
        self.font_headline = tkfont.Font(family="Arial", size=20, weight="bold")
        self.font_body = tkfont.Font(family="Arial", size=11)
        self.font_mono = tkfont.Font(family="Courier New", size=10)
        self.font_mono_bold = tkfont.Font(family="Courier New", size=16, weight="bold")

        self._build_sidebar()
        self._build_main_area()

    # -----------------------------------------------------------
    # SIDEBAR
    # -----------------------------------------------------------
    def _build_sidebar(self):
        """
        A tk.Frame is Tkinter's version of a plain box/container,
        roughly like a QFrame in PyQt or a begin_child() region in
        ImGui. side="left" + fill="y" is what actually makes this a
        SIDEBAR rather than a stacked block: it pins the frame to the
        left edge and lets it stretch to fill the window's height.

        This is Tkinter's equivalent of ImGui's same_line(): instead
        of a one-off call between two draw commands, you set a
        permanent "side" property on each widget declaring which
        edge it packs against, and Tkinter's pack() layout manager
        arranges everything around those edges automatically, every
        time the window is drawn, not just once.
        """
        sidebar = tk.Frame(self.root, bg=INK, width=SIDEBAR_WIDTH)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)  # stop the frame from shrinking to fit its contents



        # Stored as self.streak_label because log_problem() below needs
        # to reach back into this widget and change its text later.
        # This is the exact same pattern as self.streak_value in the
        # PyQt version, a persistent reference kept alive past __init__.
        self.streak_label = tk.Label(
            sidebar, text=f"Files", font=self.font_mono_bold, fg=AMBER, bg=INK
        )
        self.streak_label.pack(anchor="w", padx=SPACE_SM, pady=(SPACE_XS, 0))
        self.drag_sidebar = DragDropSidebar(sidebar)
        self.drag_sidebar.pack(fill="both", expand=True, padx=SPACE_XS, pady=(SPACE_MD, SPACE_XS))


    # -----------------------------------------------------------
    # MAIN AREA
    # -----------------------------------------------------------
    def _build_main_area(self):
        self.sim_view = SimView(self.root, "path/to/your_model.xml", width=340, height=300)
        self.sim_view.pack(side="left", fill="both", expand=True)

    # -----------------------------------------------------------
    # PLACEHOLDER TEXT
    # Tkinter's Entry has no built-in placeholder/hint text like
    # ImGui's input_text_with_hint() or Qt's setPlaceholderText().
    # You fake it: insert gray hint text manually, then clear it on
    # focus and restore it on blur if left empty. This is the kind
    # of small convenience the other two toolkits give you for free
    # that Tkinter makes you build yourself.
    # -----------------------------------------------------------
    def _set_placeholder(self):
        placeholder = "e.g. two-sum"
        self.problem_entry.insert(0, placeholder)
        self.problem_entry.config(fg=SLATE)

        def on_focus_in(event):
            if self.problem_entry.get() == placeholder:
                self.problem_entry.delete(0, "end")
                self.problem_entry.config(fg=INK)

        def on_focus_out(event):
            if self.problem_entry.get().strip() == "":
                self.problem_entry.insert(0, placeholder)
                self.problem_entry.config(fg=SLATE)

        self.problem_entry.bind("<FocusIn>", on_focus_in)
        self.problem_entry.bind("<FocusOut>", on_focus_out)

    # -----------------------------------------------------------
    # EVENT HANDLER
    # Same logic as every other version. The difference from ImGui's
    # log_problem() is purely mechanical: this one ends by calling
    # .config(text=...) on stored widget references instead of just
    # letting the next frame's draw calls pick up new state values.
    # -----------------------------------------------------------
    def log_problem(self):
        name = self.problem_entry.get().strip()
        if not name or name == "e.g. two-sum":
            self.status_label.config(text="enter a problem name first", fg=AMBER)
            return
    
    


if __name__ == "__main__":
    # -------------------------------------------------------
    # root.mainloop() is Tkinter's version of Qt's app.exec() or
    # ImGui's immapp.run(): it starts the event loop and blocks here
    # until the window closes. Unlike ImGui, this loop does NOT call
    # your code every frame, it sits idle and only wakes up your
    # code when an actual event happens (a click, a keystroke, a
    # timer). That's the real practical consequence of retained mode:
    # far less CPU used while nothing is happening, at the cost of
    # everything needing an explicit event handler instead of just
    # being redrawn from current state.
    # -------------------------------------------------------
    root = TkinterDnD.Tk()
    app = PaceApp(root)
    root.mainloop()