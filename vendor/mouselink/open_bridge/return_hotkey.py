"""Recognize a standalone Ctrl+left-Alt chord on release, without hooks."""


class ReturnChord:
    CONTROL = {0xA2, 0xA3}
    ALT = 0xA4
    MODIFIERS = CONTROL | {ALT}

    def __init__(self):
        self.reset()

    def reset(self):
        self.down = set()
        self.armed = False
        self.cancelled = False

    def press(self, vk):
        if vk in self.down:
            return
        self.down.add(vk)
        if vk not in self.MODIFIERS:
            self.cancelled = True
        if self.down & self.CONTROL and self.ALT in self.down:
            self.armed = True

    def release(self, vk):
        if vk not in self.down:
            return False
        self.down.remove(vk)
        if self.down & self.MODIFIERS:
            return False
        trigger = self.armed and not self.cancelled and not self.down
        self.armed = False
        self.cancelled = bool(self.down)
        return trigger
