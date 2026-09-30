"""Pure preview state machine. No device, registry, config or engine imports."""
from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    DISPLAY = "display"
    BRIDGE = "bridge"


class State(str, Enum):
    READY = "可用"
    MISSING = "缺少条件"
    CONNECTING = "连接中"
    ACTIVE = "正在使用"
    STOPPING = "正在停止"
    FAILED = "失败"


@dataclass
class PreviewSession:
    selected: Mode = Mode.DISPLAY
    owner: Mode | None = None
    state: State = State.READY
    pending: Mode | None = None
    epoch: int = 0
    scenario: str = "正常连接"
    busy_tool: bool = False
    release_blocked: bool = False

    def request_start(self, target: Mode) -> int | None:
        if self.busy_tool or self.release_blocked or self.state in (State.CONNECTING, State.STOPPING):
            return None
        if self.owner == target and self.state == State.ACTIVE:
            return None
        self.epoch += 1
        self.selected = target
        if self.owner is not None:
            self.pending = target
            self.state = State.STOPPING
        else:
            self._begin(target)
        return self.epoch

    def _begin(self, target: Mode) -> None:
        self.selected = target
        self.pending = None
        if self.scenario == "缺少条件":
            self.state = State.MISSING
            self.owner = None
        else:
            self.owner = target
            self.state = State.CONNECTING

    def advance(self, token: int) -> bool:
        if token != self.epoch:
            return False
        if self.state == State.STOPPING:
            if self.scenario == "停止失败":
                self.pending = None
                self.release_blocked = True
                self.state = State.FAILED
            else:
                self.owner = None
                if self.pending is not None:
                    self._begin(self.pending)
                else:
                    self.state = State.READY
        elif self.state == State.CONNECTING:
            if self.scenario == "连接失败":
                self.owner = None
                self.state = State.FAILED
            else:
                self.state = State.ACTIVE
        else:
            return False
        return True

    def stop(self) -> int | None:
        if self.busy_tool or self.release_blocked:
            return None
        self.epoch += 1
        self.pending = None
        self.state = State.STOPPING if self.owner else State.READY
        return self.epoch

    def recover(self) -> int | None:
        if not self.release_blocked:
            return None
        self.epoch += 1
        self.state = State.STOPPING
        self.release_blocked = False
        self.scenario = "正常连接"
        return self.epoch

    def begin_tool(self) -> bool:
        if self.owner is not None or self.busy_tool or self.release_blocked:
            return False
        self.busy_tool = True
        self.epoch += 1
        return True

    def finish_tool(self, token: int) -> bool:
        if token != self.epoch or not self.busy_tool:
            return False
        self.busy_tool = False
        return True

    def reset(self) -> None:
        self.epoch += 1
        self.owner = self.pending = None
        self.state = State.READY
        self.release_blocked = self.busy_tool = False

