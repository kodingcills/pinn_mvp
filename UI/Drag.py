import tkinter as tk
from tkinter import font as tkfont
from tkinterdnd2 import TkinterDnD, DND_FILES

PAPER = "#F7F5F0"
INK = "#1C1D21"
SLATE = "#4B5468"
AMBER = "#E8862E"
INK_SURFACE = "#35363C" 
AMBER_DIM = "#6E4726"



SPACE_XS, SPACE_SM, SPACE_MD = 8, 16, 24
SIDEBAR_WIDTH = 140


class DragDropSidebar(tk.Frame):
    def __init__(self, parent, width=SIDEBAR_WIDTH):
        super().__init__(parent, bg=INK, width=width)   # <- what goes here? think about
                                                        #    what tk.Frame.__init__ needs,
                                                        #    you're building yourself AS a frame now
        self.pack_propagate(False)

        self.lb = tk.Listbox(
            self,                # <- parent for the listbox... who's "self" now?
            bg=INK_SURFACE, fg=PAPER,
            selectbackground=AMBER, selectforeground=INK,
            highlightbackground=AMBER_DIM, highlightthickness=0,
            relief="flat", borderwidth=0,
        )
        self.lb.insert(0, "Drop File Here")
        self.lb.pack(fill="both", expand=True, padx=SPACE_XS, pady=SPACE_XS)

        self.lb.drop_target_register(DND_FILES)
        self.lb.dnd_bind('<<Drop>>', self.on_drop)   # <- can't just be a loose function
                                                   #    anymore, needs to be a method
                                                   #    on this class. how do you write
                                                   #    on_drop as a method instead?

    def on_drop(self, event):
        for path in self.tk.splitlist(event.data):
            self.lb.insert(tk.END, path)