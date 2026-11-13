"""Billiards physics for desktop icons, in desktop pixels.

The table is laid out inside the monitor work area, the white cue ball starts on
the left and the desktop icons are racked on the right. The caller aims with
:meth:`aim_at` and strikes with :meth:`shoot`; :meth:`step` advances the whole
simulation, including pocket exits and the ball-in-hand state after a scratch.

LAN versus (see ``docs/NETPLAY.md``) reuses the same class: both machines build the
table from the shared logical bounds with a fixed ball count, so the rack, the rails
and the pockets come out identical whatever the local icon sizes are. Only the host
integrates the physics; the mirroring client feeds host snapshots into
:meth:`apply_snapshot` and records local intents in :attr:`pending_shot` and
:attr:`pending_place` instead of touching the balls.
"""
import math
from collections import defaultdict

from .body import Body

# How long a sinking icon keeps its exit animation.
EXIT_SECONDS = .62
MIN_SHOT_CHARGE = .18
RAIL_REBOUND = .88
BALL_IMPULSE = .96
ROLLING_DRAG = .45
STOP_SPEED = .008

# The one table both versus machines build. ``netplay`` publishes the same values for the
# transport and the renderer; table keeps its own copy so this module stays standalone.
LOGICAL_BOUNDS = (0., 0., 1600., 900.)
VERSUS_BALLS = 15


class BilliardsMotion:
    """Positions and physics of the white ball and the desktop icon rack."""

    def __init__(self, work_area, sizes, count=None):
        self.sizes = sizes
        # The game is always drawn directly on the desktop and always manual.
        self.desktop = True
        self.manual = True
        self.work_area = work_area
        # LAN versus bookkeeping (docs/NETPLAY.md section 4). A single-player table stays
        # authoritative on side 0, which keeps the default path bit-for-bit unchanged.
        self.versus = False
        self.turn = 0
        self.scores = [0, 0]
        self.outcome = 0
        self.authoritative = True
        self.local_side = 0
        self.remote_ms = None
        self.pending_shot = None
        self.pending_place = None
        self.shooter = 0
        self.potted_this_shot = 0
        # ``count=None`` keeps the original rule: one ball per icon.
        self._layout(len(sizes) if count is None else count)
        # Counts every (re)started round so observers can tell rounds apart.
        self.round_serial = 0
        self.reset()

    def _layout(self, count):
        """Size the bounded table and rack ``count`` balls on it.

        Only the work area and the ball count decide the geometry, so two machines that
        pass the same logical bounds build the same table whatever their icon sizes are.
        """
        self.count = max(0, int(count))
        left, top, right, bottom = self.work_area
        width = right - left
        height = bottom - top
        self.bounds = (round(left + width * .06), round(top + height * .09),
                       round(right - width * .06), round(bottom - height * .09))
        left, top, right, bottom = self.bounds
        self.width = right - left
        self.height = bottom - top
        mid = (left + right) / 2
        self.pockets = ((left, top), (mid, top), (right, top),
                        (left, bottom), (mid, bottom), (right, bottom))
        self.pocket_radius = min(49, max(18, self.height * .047))
        columns = max(1, math.ceil((math.sqrt(8 * self.count + 1) - 1) / 2))
        pitch = min(94, self.width * .45 / (columns + .5), self.height * .79 / columns)
        self.pitch = pitch
        self.tile = min(64, pitch * .70)
        self.radius = pitch * .46
        self.white_radius = min(25, self.radius * .9)
        self.rack = []
        for column in range(columns):
            depth = min(column + 1, self.count - len(self.rack))
            for i in range(depth):
                x = left + self.width * .52 + column * pitch * .84
                y = (top + bottom) / 2 + (i - (depth - 1) / 2) * pitch * .95
                self.rack.append((x, y))

    def reset(self):
        left, top, _, bottom = self.bounds
        self.white = Body(left + self.width * .20, (top + bottom) / 2, self.white_radius, self.bounds)
        self.icons = [Body(x, y, self.radius, self.bounds) for x, y in self.rack]
        self.bodies = [self.white, *self.icons]
        self.sunk = set()
        self.white_sunk = False
        self.exits = {}
        self.state = 'ready'
        self.ready_for = 0.
        # The timed round runs from the first shot to the last icon sinking.
        self.round_serial += 1
        self.first_shot_at = None
        self.finished_at = None
        self.paused_at = None
        self.shots = 0
        self.clock = 0.
        self.stroke = 0.
        self.power = 0.
        self.charge = 0.
        self.break_shot = True
        # Versus bookkeeping that is bound to a single stroke, not to the match.
        self.shooter = self.turn
        self.potted_this_shot = 0
        self.aim = self._aim()
        self.poses = self._poses()

    def active_bodies(self):
        return [body for index, body in enumerate(self.bodies) if index not in self.sunk]

    @property
    def pocketed_icons(self):
        return len(self.sunk) - (0 in self.sunk)

    @property
    def play_ms(self):
        """Whole milliseconds since the first shot; frozen once the table is cleared.

        A versus guest shows the host's clock through :attr:`remote_ms` instead of its own.
        """
        if self.remote_ms is not None:
            return max(0, int(round(self.remote_ms)))
        if self.first_shot_at is None:
            return 0
        end = self.finished_at if self.finished_at is not None else self.clock
        return max(0, round((end - self.first_shot_at) * 1000))

    @property
    def round_seconds(self):
        return self.play_ms // 1000

    def pause(self, now):
        """Freeze the round (the game is hidden). Nothing is simulated until :meth:`resume`."""
        if self.paused_at is None:
            self.paused_at = now

    def resume(self, now):
        """Continue after a pause; the hidden time does not count towards the round."""
        if self.paused_at is None:
            return
        gap = max(0., now - self.paused_at)
        if self.first_shot_at is not None and self.finished_at is None:
            self.first_shot_at += gap
        self.paused_at = None

    # -- LAN versus (docs/NETPLAY.md sections 4 and 5) ----------------------
    def start_versus(self, count=VERSUS_BALLS, first=0):
        """Restart as a versus round: fixed rack, clean scores and the given first shooter.

        ``authoritative`` and ``local_side`` belong to the role and are left alone, so the
        overlay can set them once when the session is established.
        """
        self._layout(count)
        self.versus = True
        self.turn = 1 if first else 0
        self.scores = [0, 0]
        self.outcome = 0
        self.remote_ms = None
        self.pending_shot = None
        self.pending_place = None
        self.reset()

    def can_shoot(self):
        """Whether this machine may strike the white ball right now."""
        return self.state == 'ready' and int(self.turn) == int(self.local_side)

    def snapshot(self):
        """Return the JSON-safe table state a versus host ships to its guest."""
        return {
            'serial': int(self.round_serial),
            'state': self.state,
            'white': [round(self.white.x, 2), round(self.white.y, 2)],
            'white_sunk': 1 if self.white_sunk else 0,
            'balls': [[round(body.x, 2), round(body.y, 2), round(body.angle, 2),
                       round(body.impact, 2)] for body in self.icons],
            'sunk': [1 if index in self.sunk else 0 for index in range(len(self.bodies))],
            'exits': {str(index): [round(x, 2), round(y, 2), round(tx, 2), round(ty, 2),
                                   round(age, 2)]
                      for index, (x, y, tx, ty, age) in self.exits.items()},
            'turn': int(self.turn),
            'scores': [int(self.scores[0]), int(self.scores[1])],
            'shots': int(self.shots),
            'ms': self.play_ms,
            'outcome': int(self.outcome),
            'charge': round(self.charge, 2),
        }

    def apply_snapshot(self, state):
        """Mirror a host snapshot into this table, without starting a new round."""
        balls = state.get('balls')
        if balls is not None and len(balls) != len(self.icons):
            # The host restarted with another ball count: rebuild the rack, then let the
            # snapshot below overwrite every position. reset() bumps round_serial, but the
            # serial carried by a snapshot always wins at the end of this method.
            self._layout(len(balls))
            self.reset()
        white = state.get('white')
        if white:
            self.white.x, self.white.y = float(white[0]), float(white[1])
        # The guest never simulates, so it must not keep velocities of its own either.
        self.white.vx = self.white.vy = 0.
        for body, values in zip(self.icons, balls or ()):
            body.x, body.y = float(values[0]), float(values[1])
            body.angle = float(values[2])
            body.impact = float(values[3])
            body.vx = body.vy = 0.
        sunk = state.get('sunk') or ()
        self.sunk = {index for index, flag in enumerate(sunk) if flag and index < len(self.bodies)}
        self.white_sunk = bool(state.get('white_sunk'))
        self.exits = {int(index): (float(values[0]), float(values[1]), float(values[2]),
                                   float(values[3]), float(values[4]))
                      for index, values in (state.get('exits') or {}).items()}
        if state.get('state') is not None:
            self.state = str(state['state'])
        self.turn = 1 if state.get('turn') else 0
        scores = state.get('scores') or ()
        if len(scores) >= 2:
            self.scores = [int(scores[0]), int(scores[1])]
        if state.get('shots') is not None:
            self.shots = int(state['shots'])
        if state.get('outcome') is not None:
            self.outcome = int(state['outcome'])
        if state.get('ms') is not None:
            self.remote_ms = int(state['ms'])
        if state.get('charge') is not None:
            # Display only: the overlay re-applies the local power while this side aims.
            self.charge = float(state['charge'])
        serial = state.get('serial')
        if isinstance(serial, int) and serial != self.round_serial:
            # A different serial means the host started a new round: adopt it, but never
            # reset locally, because the snapshot itself carries the fresh table.
            self.round_serial = serial
        self.poses = self._poses()

    # -- aiming and striking ----------------------------------------------
    def _aim(self):
        x, y = self.white.x, self.white.y
        targets = [body for index, body in enumerate(self.icons, 1) if index not in self.sunk]
        target = min(targets, key=lambda body: (body.x - x) ** 2 + (body.y - y) ** 2, default=None)
        if target is None:
            return 1., 0.
        if self.break_shot:
            return 1., .022
        shot = self._pocket_shot(targets)
        dx, dy = (shot[0] - x, shot[1] - y) if shot else (target.x - x, target.y - y)
        distance = max(1, math.hypot(dx, dy))
        return dx / distance, dy / distance

    @staticmethod
    def _clear(x, y, tx, ty, others, radius):
        dx, dy = tx - x, ty - y
        length = dx * dx + dy * dy
        for other in others:
            t = max(0, min(1, ((other.x - x) * dx + (other.y - y) * dy) / max(1, length)))
            if math.hypot(other.x - x - t * dx, other.y - y - t * dy) < other.radius + radius:
                return False
        return True

    def _pocket_shot(self, targets):
        white = self.white
        left, top, right, bottom = self.bounds
        candidates = []
        for target in targets:
            for px, py in self.pockets:
                dx, dy = px - target.x, py - target.y
                distance = math.hypot(dx, dy)
                if distance < 1:
                    continue
                ux, uy = dx / distance, dy / distance
                contact = target.radius + white.radius
                gx, gy = target.x - ux * contact, target.y - uy * contact
                if not (left + white.radius <= gx <= right - white.radius
                        and top + white.radius <= gy <= bottom - white.radius):
                    continue
                wx, wy = gx - white.x, gy - white.y
                approach = math.hypot(wx, wy)
                alignment = (wx * ux + wy * uy) / max(1, approach)
                if alignment > .53:
                    candidates.append(((approach + distance) / alignment, target, px, py, gx, gy))
        for _, target, px, py, gx, gy in sorted(candidates, key=lambda shot: shot[0]):
            others = [body for body in targets if body is not target]
            if (self._clear(white.x, white.y, gx, gy, others, white.radius)
                    and self._clear(target.x, target.y, px, py, others, target.radius)):
                return gx, gy
        return None

    def aim_at(self, x, y):
        if self.state != 'ready':
            return
        dx, dy = x - self.white.x, y - self.white.y
        length = math.hypot(dx, dy)
        if length > 2:
            self.aim = (dx / length, dy / length)

    def shoot(self, power, now=None):
        """Strike the white ball. ``now`` is the frame clock; the first shot starts the round timer."""
        if not self.authoritative:
            # The mirroring client only records the intent; the host owns the physics.
            self.pending_shot = (self.aim, power)
            return False
        if self.state != 'ready':
            return False
        self.state = 'stroke'
        self.stroke = 0.
        self.power = self._shot_speed(power)
        self.break_shot = False
        self.shots += 1
        self.shooter = self.turn
        self.potted_this_shot = 0
        if self.first_shot_at is None:
            self.first_shot_at = self.clock if now is None else now
            self.clock = max(self.clock, self.first_shot_at)
        return True

    def _shot_speed(self, power):
        return self.height * (.62 + 2.55 * max(0, min(1, power))) * 1.58

    def cue(self):
        if self.state not in ('ready', 'stroke'):
            return None
        dx, dy = self.aim
        pull = 12 + 34 * self.charge
        gap = pull * max(0, 1 - self.stroke / .082) if self.state == 'stroke' else pull
        tip = (self.white.x - dx * (self.white.radius + gap), self.white.y - dy * (self.white.radius + gap))
        length = min(440, self.height * .48)
        left, top, right, bottom = self.work_area
        for value, direction, low, high in ((tip[0], -dx, left + 5, right - 5),
                                            (tip[1], -dy, top + 5, bottom - 5)):
            if abs(direction) > 1e-6:
                edge = high if direction > 0 else low
                length = min(length, max(0, (edge - value) / direction))
        return tip, (tip[0] - dx * length, tip[1] - dy * length)

    def aim_guide(self):
        """Predict the opening shot and one outgoing leg per moving ball."""
        return self.aim_preview()[0]

    def aim_preview(self):
        """Return ``(lines, circles)``: the predicted paths and the collision circles to show faintly.

        Collisions use circles of ``self.radius``, a little larger than the drawn icon, so the
        ``ghost`` circle (the white ball at the moment of contact) and the ``target`` circle make
        the real contact point visible.
        """
        if self.state != 'ready':
            return (), ()
        x, y, radius = self.white.x, self.white.y, self.white.radius
        dx, dy = self.aim
        speed = self._shot_speed(max(MIN_SHOT_CHARGE, self.charge))
        targets = [body for index, body in enumerate(self.icons, 1) if index not in self.sunk]
        origin = x, y
        hit, kind, contact, velocity = self._guide_leg(origin, (dx * speed, dy * speed), radius, targets)
        lines = []
        gap = radius + 5
        if math.dist(origin, hit) > gap:
            lines.append(('incoming', (x + dx * gap, y + dy * gap), hit))
        vx, vy = velocity
        outgoing = []
        circles = []
        if kind == 'rail':
            hit_x, hit_y = contact
            rebound = (-vx * RAIL_REBOUND if hit_x else vx, -vy * RAIL_REBOUND if hit_y else vy)
            outgoing.append(('white', hit, rebound, radius, targets))
            circles.append(('ghost', hit, radius))
        elif kind == 'ball':
            target = contact
            target_pos = target.x, target.y
            separation = math.dist(hit, target_pos)
            circles.append(('ghost', hit, radius))
            circles.append(('target', target_pos, target.radius))
            if separation > 1e-6:
                nx, ny = (target.x - hit[0]) / separation, (target.y - hit[1]) / separation
                impulse = (vx * nx + vy * ny) * BALL_IMPULSE
                pushed = impulse * nx, impulse * ny
                other_targets = [body for body in targets if body is not target]
                outgoing.append(('white', hit, (vx - pushed[0], vy - pushed[1]), radius, other_targets))
                outgoing.append(('target', target_pos, pushed, target.radius, other_targets))
        for role, start, motion, size, obstacles in outgoing:
            end, _, _, _ = self._guide_leg(start, motion, size, obstacles)
            if math.dist(start, end) >= 2:
                lines.append((role, start, end))
        return tuple(lines), tuple(circles)

    def _guide_leg(self, origin, velocity, radius, obstacles):
        """Return endpoint, contact type, contact data and remaining velocity."""
        x, y = origin
        speed = math.hypot(*velocity)
        stop = self.height * STOP_SPEED
        if speed <= stop:
            return origin, 'stop', None, (0., 0.)
        dx, dy = velocity[0] / speed, velocity[1] / speed
        drag = self.height * ROLLING_DRAG
        distance = (speed ** 2 - stop ** 2) / (2 * drag)
        kind, contact = 'stop', None
        left, top, right, bottom = self.bounds
        rail_x = ((right - radius if dx > 0 else left + radius) - x) / dx if abs(dx) > 1e-6 else math.inf
        rail_y = ((bottom - radius if dy > 0 else top + radius) - y) / dy if abs(dy) > 1e-6 else math.inf
        rail = min(rail_x, rail_y)
        if rail <= distance:
            distance = max(0., rail)
            kind = 'rail'
            contact = math.isclose(rail_x, rail, abs_tol=1e-6), math.isclose(rail_y, rail, abs_tol=1e-6)
        for body in obstacles:
            entry = self._ray_circle(x, y, dx, dy, body.x, body.y, radius + body.radius)
            if entry is not None and entry < distance:
                distance, kind, contact = entry, 'ball', body
        for px, py in self.pockets:
            entry = self._ray_circle(x, y, dx, dy, px, py, self.pocket_radius + radius * .55)
            if entry is not None and entry <= distance:
                distance, kind, contact = entry, 'pocket', None
        speed = math.sqrt(max(0., speed ** 2 - 2 * drag * distance))
        return (x + dx * distance, y + dy * distance), kind, contact, (dx * speed, dy * speed)

    @staticmethod
    def _ray_circle(x, y, dx, dy, cx, cy, radius):
        offset_x, offset_y = cx - x, cy - y
        forward = offset_x * dx + offset_y * dy
        across = offset_x * dy - offset_y * dx
        clearance = radius ** 2 - across ** 2
        if forward > 0 and clearance > 0:
            return max(0., forward - math.sqrt(clearance))
        return None

    def step(self, dt, now):
        """Advance the simulation with the runner's monotonic frame clock."""
        dt = max(0, min(.05, dt))
        if self.exits:
            self.exits = {i: (x, y, tx, ty, age + dt) for i, (x, y, tx, ty, age) in self.exits.items()
                          if age + dt < EXIT_SECONDS}
        if self.state == 'finished':
            return
        self.clock = now
        if not self.authoritative:
            # The guest only renders snapshots; integrating here would fight the host.
            return
        if self.state == 'return_white':
            self.ready_for += dt
            if self.ready_for >= .8:
                self.state = 'ball_in_hand'
                self._auto_place()
            return
        if self.state == 'ball_in_hand':
            return
        if self.state == 'ready':
            self.ready_for += dt
            return
        if self.state == 'stroke':
            self.stroke += dt
            if self.stroke < .082:
                return
            self.white.vx = self.aim[0] * self.power
            self.white.vy = self.aim[1] * self.power
            self.state = 'rolling'
            dt = self.stroke - .082
        speed = max((math.hypot(body.vx, body.vy) for body in self.active_bodies()), default=0)
        steps = max(1, math.ceil(dt * max(180, speed / max(.5, self.white_radius) * 1.7)))
        h = dt / steps
        for _ in range(steps):
            for index, body in enumerate(self.bodies):
                if index in self.sunk:
                    continue
                body.x += body.vx * h
                body.y += body.vy * h
                pocket = self._in_pocket(body)
                if pocket is not None:
                    self.sunk.add(index)
                    if index == 0:
                        self.white_sunk = True
                    else:
                        self.potted_this_shot += 1
                        self._start_exit(index, body, pocket)
                else:
                    self._walls(body)
            active = self.active_bodies()
            self._collisions(active)
            for body in active:
                self._walls(body)
                speed = math.hypot(body.vx, body.vy)
                if speed:
                    drag = max(0, 1 - self.height * ROLLING_DRAG * h / speed)
                    body.vx *= drag
                    body.vy *= drag
                body.impact *= math.exp(-h * 12)
                body.angle *= math.exp(-h * 4)
        cleared = self.pocketed_icons == len(self.icons)
        if cleared or max((math.hypot(body.vx, body.vy) for body in self.active_bodies()), default=0) < self.height * STOP_SPEED:
            for body in self.bodies:
                body.vx = body.vy = body.impact = body.angle = 0.
            self.charge = 0.
            self.ready_for = 0.
            if cleared:
                self.state = 'finished'
                self.finished_at = now
            elif self.white_sunk:
                self.state = 'return_white'
            else:
                self.state = 'ready'
                self.aim = self._aim()
            if self.versus:
                self._settle(cleared)
        self.poses = self._poses()

    def _settle(self, cleared):
        """Score the stroke that just stopped and hand the table to the right side.

        The five versus rules are applied in the contract order: score the potted icons,
        finish on a cleared table, hand over after a scratch, keep shooting after a pot and
        otherwise pass the turn.
        """
        shooter = self.shooter
        potted = self.potted_this_shot
        self.potted_this_shot = 0
        self.scores[shooter] += potted
        if cleared:
            self.state = 'finished'
            if self.scores[0] > self.scores[1]:
                self.outcome = 1
            elif self.scores[1] > self.scores[0]:
                self.outcome = 2
            else:
                self.outcome = 3
            return
        if self.white_sunk or not potted:
            # A scratch is a foul, and a dry stroke passes the table either way.
            self.turn = 1 - shooter
        else:
            self.turn = shooter

    def _in_pocket(self, body):
        capture = self.pocket_radius + body.radius * .55
        return next(((x, y) for x, y in self.pockets
                     if (body.x - x) ** 2 + (body.y - y) ** 2 < capture * capture), None)

    def _start_exit(self, index, body, pocket):
        # The icon is drawn into the pocket centre, which always lies inside the work area.
        px, py = pocket
        self.exits[index] = (body.x, body.y, px, py, 0.)

    def exit_visual(self, index):
        """Return ``(x, y, fade, scale, spin)`` for a sinking icon, or None once it is gone.

        The icon slides to the pocket centre while shrinking and turning, then fades out.
        """
        exit_state = self.exits.get(index)
        if exit_state is None:
            return None
        x, y, tx, ty, age = exit_state
        progress = min(1., age / EXIT_SECONDS)
        slide = min(1., progress / .5)
        travel = slide * slide * (3 - 2 * slide)
        fade = max(0., min(1., (progress - .40) / .60))
        shrink = max(0., min(1., (progress - .10) / .80))
        scale = 1 - .70 * shrink * shrink * (3 - 2 * shrink)
        left, _, right, _ = self.work_area
        spin = (260 if tx < (left + right) / 2 else -260) * progress ** 2
        return x + (tx - x) * travel, y + (ty - y) * travel, fade, scale, spin

    def place_white(self, x, y):
        if not self.authoritative:
            # Mirroring client: remember the request for the host and report "not placed".
            self.pending_place = (x, y)
            return False
        if self.state != 'ball_in_hand':
            return False
        left, top, right, bottom = self.bounds
        r = self.white.radius
        if not (left + r <= x <= right - r and top + r <= y <= bottom - r):
            return False
        if any(math.hypot(x - px, y - py) < self.pocket_radius + r for px, py in self.pockets):
            return False
        if any(math.hypot(x - body.x, y - body.y) < r + body.radius + 2 for body in self.active_bodies()):
            return False
        self.white.x = x
        self.white.y = y
        self.sunk.discard(0)
        self.white_sunk = False
        self.state = 'ready'
        self.ready_for = 0.
        self.aim = self._aim()
        return True

    def _auto_place(self):
        left, top, _, _ = self.bounds
        for fx in (.20, .31, .42, .54, .66, .78):
            for fy in (.5, .31, .69, .19, .81):
                if self.place_white(left + self.width * fx, top + self.height * fy):
                    return True
        return False

    def _walls(self, body):
        left, top, right, bottom = self.bounds
        for axis, velocity, low, high in (('x', 'vx', left + body.radius, right - body.radius),
                                          ('y', 'vy', top + body.radius, bottom - body.radius)):
            position = getattr(body, axis)
            speed = getattr(body, velocity)
            if position < low or position > high:
                setattr(body, axis, max(low, min(high, position)))
                if (position < low and speed < 0) or (position > high and speed > 0):
                    setattr(body, velocity, -speed * RAIL_REBOUND)
                    body.impact = max(body.impact, min(.23, abs(speed) / self.height * .25))

    def _collisions(self, bodies):
        # Nearby buckets keep the number of tested pairs low on crowded desktops.
        cell = max(1, self.radius * 2)
        buckets = defaultdict(list)
        for body in bodies:
            gx = math.floor(body.x / cell)
            gy = math.floor(body.y / cell)
            for y in range(gy - 1, gy + 2):
                for x in range(gx - 1, gx + 2):
                    for other in buckets.get((x, y), ()):
                        self._collide(other, body)
            buckets[gx, gy].append(body)

    def _collide(self, a, b):
        dx = b.x - a.x
        dy = b.y - a.y
        limit = a.radius + b.radius
        if dx * dx + dy * dy >= limit * limit:
            return
        distance = math.hypot(dx, dy)
        nx, ny = (dx / distance, dy / distance) if distance > 1e-6 else (1., 0.)
        correction = (limit - distance) * .5
        a.x -= nx * correction
        a.y -= ny * correction
        b.x += nx * correction
        b.y += ny * correction
        closing = (b.vx - a.vx) * nx + (b.vy - a.vy) * ny
        if closing >= 0:
            return
        impulse = -closing * BALL_IMPULSE
        a.vx -= nx * impulse
        a.vy -= ny * impulse
        b.vx += nx * impulse
        b.vy += ny * impulse
        impact = min(.23, impulse / self.height * .30)
        a.impact = max(a.impact, impact)
        b.impact = max(b.impact, impact)
        a.angle -= ny * impact * 95
        b.angle += ny * impact * 95

    def _poses(self):
        result = []
        for index, body in enumerate(self.icons):
            if index < len(self.sizes):
                w, h = self.sizes[index]
            else:
                # A forced ball count can exceed the icons on this desktop: spare balls
                # borrow a square as wide as the tile instead of disappearing from the rack.
                w = h = self.tile
            ratio = self.tile / max(w, h)
            result.append((body.x, body.y, max(1, round(w * ratio * (1 + body.impact * .64))),
                           max(1, round(h * ratio * (1 - body.impact))), body.angle, 0, 0.))
        return result
