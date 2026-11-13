"""Host-authoritative LAN versus pump, independent of the desktop renderer.

The renderer owns windows and pixels; this module owns the conversation between one
machine's :class:`~dib.table.BilliardsMotion` and one :class:`~dib.netplay.Session`.
Keeping it separate means the whole turn/traffic protocol can be exercised headlessly,
with two tables and two real sockets, instead of only by watching two desktops.

Rules recap (``docs/NETPLAY.md`` section 4): the host integrates the physics and ships
:meth:`BilliardsMotion.snapshot`; the guest only mirrors those snapshots and records
shots or ball placements in ``pending_shot`` / ``pending_place``, which this pump turns
back into ``shot`` / ``place`` messages.
"""
from __future__ import annotations

from .netplay import STATE_HZ, VERSUS_BALLS


class VersusLink:
    """Drive one side of a match: drain the wire, apply it, publish what changed."""

    def __init__(self, table, session, role):
        if role not in ('host', 'guest'):
            raise ValueError(f'unknown versus role: {role!r}')
        self.table = table
        self.session = session
        self.role = role
        # Player-facing state the UI reads.
        self.disconnect_reason = ''
        self.nack_note = ''
        self.local_seq = 0
        self.last_state_at = 0.0
        self.last_state_signature = None

    @property
    def authoritative(self):
        return bool(self.table.authoritative)

    # -- local input -------------------------------------------------------
    def pointer_active(self):
        """Whether the local player may aim, charge or place the white ball right now."""
        table = self.table
        if int(getattr(table, 'turn', 0)) != int(getattr(table, 'local_side', 0)):
            return False
        return table.state in ('ready', 'ball_in_hand')

    def restart(self, now=None):
        """Ask for a new round: the host resets, the guest requests one."""
        table = self.table
        if table.authoritative:
            table.start_versus(VERSUS_BALLS, first=1 - int(table.turn))
            self.last_state_signature = None
        else:
            self.session.send({'t': 'restart', 'v': 1})

    # -- the pump ----------------------------------------------------------
    def pump(self, now):
        """One frame of traffic. Call once per rendered frame, before stepping physics."""
        session = self.session
        table = self.table
        if session is None or table is None:
            return
        for message in session.drain():
            try:
                self.handle(message, now)
            except Exception:
                # A malformed or hostile frame must never kill the animation thread.
                self.nack_note = '收到无法识别的消息'
        status = getattr(session, 'status', 'closed')
        if status in ('closed', 'error'):
            if not self.disconnect_reason:
                error = getattr(session, 'error', None)
                self.disconnect_reason = str(error) if error else '与对手的连接已断开'
            return
        if not table.authoritative:
            self.flush()
            return
        signature = (table.round_serial, table.state, int(table.turn),
                     tuple(int(value) for value in table.scores), int(table.outcome))
        due = now - self.last_state_at >= 1 / max(1.0, STATE_HZ)
        if due or signature != self.last_state_signature:
            self.last_state_at = now
            self.last_state_signature = signature
            session.send({'t': 'state', 'v': 1, 'state': table.snapshot(), 'ms': table.play_ms})

    def handle(self, message, now):
        """Apply one inbound message; the host validates intents, the guest mirrors state."""
        table = self.table
        session = self.session
        kind = message.get('t')
        if kind == 'state':
            if not table.authoritative and isinstance(message.get('state'), dict):
                table.apply_snapshot(message['state'])
                self.nack_note = ''
        elif kind == 'shot':
            if not table.authoritative:
                return
            # This message always comes from the guest, so the host only honours it while
            # the guest actually holds the table.
            guest_side = 1 - int(table.local_side)
            if table.state != 'ready' or int(table.turn) != guest_side:
                session.send({'t': 'nack', 'v': 1, 'seq': message.get('seq'), 'why': '还没轮到你'})
                return
            aim = message.get('aim') or (0.0, 0.0)
            try:
                table.aim = (float(aim[0]), float(aim[1]))
                fired = table.shoot(float(message.get('power', .5)), now)
            except (TypeError, ValueError, IndexError):
                fired = False
            if not fired:
                session.send({'t': 'nack', 'v': 1, 'seq': message.get('seq'), 'why': '现在不能击球'})
        elif kind == 'place':
            if not table.authoritative:
                return
            try:
                placed = table.place_white(float(message.get('x', 0)), float(message.get('y', 0)))
            except (TypeError, ValueError):
                placed = False
            if not placed:
                session.send({'t': 'nack', 'v': 1, 'seq': message.get('seq'), 'why': '这个位置不能摆球'})
        elif kind == 'restart':
            if table.authoritative:
                table.start_versus(VERSUS_BALLS, first=1 - int(table.turn))
                self.last_state_signature = None
        elif kind == 'nack':
            self.nack_note = str(message.get('why') or '')
        elif kind == 'bye':
            self.disconnect_reason = str(message.get('why') or '对手离开了房间')

    def flush(self):
        """Send the intents the mirroring table only recorded."""
        table = self.table
        session = self.session
        if table is None or session is None:
            return
        shot = getattr(table, 'pending_shot', None)
        if shot:
            table.pending_shot = None
            self.local_seq += 1
            aim, power = shot
            session.send({'t': 'shot', 'v': 1, 'seq': self.local_seq,
                          'aim': [round(float(aim[0]), 4), round(float(aim[1]), 4)],
                          'power': round(float(power), 4)})
        place = getattr(table, 'pending_place', None)
        if place:
            table.pending_place = None
            self.local_seq += 1
            session.send({'t': 'place', 'v': 1, 'seq': self.local_seq,
                          'x': round(float(place[0]), 2), 'y': round(float(place[1]), 2)})
