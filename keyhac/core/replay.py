"""Keyboard macro record/playback buffer (ported from keyhac-mac
keyhac_replay.py; normalization drops unmatched key-downs)."""

from keyhac.core import log

logger = log.getLogger("Replay")


class KeyReplayBuffer:

    def __init__(self):
        self.recording = False
        self.seq: list[tuple[int, bool]] = []
        self.max_seq = 1000

    def record(self, vk: int, down: bool = True) -> None:
        if self.recording:
            if len(self.seq) >= self.max_seq:
                logger.error("Key replay buffer is full")
                return
            self.seq.append((vk, down))

    def start_recording(self) -> None:
        self.seq = []
        self.recording = True
        logger.info("Recording started")

    def stop_recording(self) -> None:
        if self.recording:
            key_table = [False] * 256
            normalized = []
            for vk, down in self.seq:
                if down:
                    if vk < 256:
                        key_table[vk] = True
                    normalized.append([vk, down, False])  # finalized on key-up
                else:
                    if vk < 256 and key_table[vk]:
                        key_table[vk] = False
                        for i in range(len(normalized) - 1, -1, -1):
                            if normalized[i][0] == vk:
                                if normalized[i][1]:
                                    if not normalized[i][2]:
                                        normalized[i][2] = True
                                    else:
                                        break
                                else:
                                    break
                        normalized.append([vk, down, True])
            self.seq = [(vk, down) for vk, down, final in normalized if final]
            self.recording = False
        logger.info("Recording stopped")

    def toggle_recording(self) -> None:
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def clear(self) -> None:
        self.seq = []
        self.recording = False
        logger.info("Cleared buffer")

    def playback(self) -> None:
        from keyhac.core.keymap import Keymap

        if self.recording:
            logger.warning("Still recording - canceling playback")
            return
        if not self.seq:
            logger.warning("Replay buffer is empty")
            return

        logger.info("Playing")
        keymap = Keymap.get_instance()
        with keymap.get_input_context(replay=True) as ctx:
            ctx.send_modifier_keys(0)
            for vk, down in self.seq:
                ctx.send_key_by_vk(vk, down)
