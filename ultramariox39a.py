#!/usr/bin/env python3
"""
Ultra Mario Bros. X 39A
========================

A clean-room, single-file, original platform game and level editor inspired by
the flexible community platformers of the late 2000s.  It does not contain or
load Nintendo/SMBX code, levels, graphics, music, or other extracted assets.

Requirements: Python 3.14+ and pygame-ce 2.5+
Run:          python3 program.py
Self-test:    python3 program.py --self-test

FILES_OFF means that every shipped asset and level is generated below.  The
editor keeps work in memory and can copy/paste compressed level codes, so no
sidecar asset files are required.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import random
import struct
import sys
import time
import zlib
from array import array
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Iterable

try:
    import pygame
except ImportError as exc:  # Friendly terminal error instead of a traceback.
    raise SystemExit(
        "Ultra Mario Bros. X 39A needs pygame-ce. Install it with:\n"
        "  python3 -m pip install pygame-ce"
    ) from exc


FILES_OFF = True
TITLE = "Ultra Mario Bros. X 39A"
VERSION = "39A.0"
LOGICAL_W, LOGICAL_H = 960, 540
FPS = 60
TILE = 32
GRAVITY = 1850.0
MAX_FALL = 900.0
SKY = (63, 139, 211)
INK = (19, 26, 45)
WHITE = (245, 247, 255)
CYAN = (102, 204, 255)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def approach(value: float, target: float, amount: float) -> float:
    if value < target:
        return min(target, value + amount)
    return max(target, value - amount)


def rect_from_floats(x: float, y: float, w: int, h: int) -> pygame.Rect:
    return pygame.Rect(round(x), round(y), w, h)


def text(surface: pygame.Surface, font: pygame.font.Font, value: str,
         pos: tuple[int, int], color: tuple[int, int, int] = WHITE,
         anchor: str = "topleft", shadow: bool = True) -> pygame.Rect:
    image = font.render(str(value), True, color)
    rect = image.get_rect()
    setattr(rect, anchor, pos)
    if shadow:
        shade = font.render(str(value), True, (8, 12, 24))
        surface.blit(shade, rect.move(2, 3))
    surface.blit(image, rect)
    return rect


class Scene(Enum):
    TITLE = auto()
    WORLD = auto()
    PLAY = auto()
    EDITOR = auto()
    OPTIONS = auto()
    HELP = auto()
    CREDITS = auto()


class Audio:
    """Tiny procedural stereo synthesizer with a silent fallback."""

    def __init__(self) -> None:
        self.ready = False
        self.enabled = True
        self.volume = 0.45
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44_100, size=-16, channels=2, buffer=512)
            self.ready = True
            specs = {
                "jump": (520, 0.09, "square", 180),
                "coin": (1050, 0.08, "sine", 450),
                "hurt": (190, 0.18, "noise", -90),
                "stomp": (135, 0.08, "square", -40),
                "power": (440, 0.28, "sine", 640),
                "fire": (760, 0.07, "square", -220),
                "break": (250, 0.11, "noise", -80),
                "warp": (300, 0.25, "sine", 850),
                "goal": (660, 0.55, "square", 440),
                "click": (380, 0.045, "square", 80),
                "tick": (880, 0.035, "sine", 0),
                "boss": (92, 0.22, "square", -20),
            }
            for name, spec in specs.items():
                self.sounds[name] = self._tone(*spec)
        except (pygame.error, NotImplementedError):
            self.ready = False

    def _tone(self, hz: float, seconds: float, wave: str, sweep: float) -> pygame.mixer.Sound:
        rate = 44_100
        frames = max(1, int(rate * seconds))
        data = array("h")
        phase = 0.0
        rng = random.Random(int(hz * 71 + frames))
        for i in range(frames):
            t = i / rate
            freq = max(30.0, hz + sweep * t / max(seconds, 0.001))
            phase += math.tau * freq / rate
            env = (1.0 - i / frames) ** 1.6
            if wave == "square":
                sample = 1.0 if math.sin(phase) >= 0 else -1.0
            elif wave == "noise":
                sample = rng.uniform(-1.0, 1.0)
            else:
                sample = math.sin(phase)
            value = int(10_500 * env * sample)
            data.extend((value, value))
        return pygame.mixer.Sound(buffer=data.tobytes())

    def play(self, name: str, volume: float = 1.0) -> None:
        if self.ready and self.enabled and name in self.sounds:
            sound = self.sounds[name]
            sound.set_volume(clamp(self.volume * volume, 0.0, 1.0))
            sound.play()


@dataclass
class Controls:
    left: int
    right: int
    up: int
    down: int
    jump: int
    run: int
    alt: int


P1_KEYS = Controls(
    pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN,
    pygame.K_z, pygame.K_x, pygame.K_c,
)
P2_KEYS = Controls(
    pygame.K_a, pygame.K_d, pygame.K_w, pygame.K_s,
    pygame.K_f, pygame.K_g, pygame.K_h,
)


@dataclass
class TileDef:
    name: str
    solid: bool = False
    hurt: bool = False
    one_way: bool = False
    breakable: bool = False
    bumpable: bool = False
    color: tuple[int, int, int] = (255, 255, 255)


TILES: dict[str, TileDef] = {
    "ground": TileDef("Ground", solid=True, color=(130, 81, 50)),
    "brick": TileDef("Brick", solid=True, breakable=True, bumpable=True, color=(190, 76, 48)),
    "question": TileDef("Bonus Block", solid=True, bumpable=True, color=(244, 179, 36)),
    "used": TileDef("Used Block", solid=True, color=(154, 119, 75)),
    "stone": TileDef("Stone", solid=True, color=(102, 115, 132)),
    "ice": TileDef("Ice", solid=True, color=(150, 232, 250)),
    "cloud": TileDef("Cloud Platform", one_way=True, color=(244, 250, 255)),
    "spike": TileDef("Spikes", hurt=True, color=(210, 222, 235)),
    "lava": TileDef("Lava", hurt=True, color=(255, 73, 28)),
    "water": TileDef("Water", color=(45, 132, 218)),
    "vine": TileDef("Climbable Vine", color=(43, 160, 62)),
    "decor": TileDef("Scenery", color=(76, 190, 95)),
}


@dataclass
class TileCell:
    x: int
    y: int
    kind: str
    layer: str = "main"
    active: bool = True
    bump: float = 0.0
    payload: str = "coin"

    @property
    def rect(self) -> pygame.Rect:
        return pygame.Rect(self.x * TILE, self.y * TILE - round(self.bump), TILE, TILE)


@dataclass
class EntitySpec:
    kind: str
    x: float
    y: float
    layer: str = "main"
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Warp:
    entrance: pygame.Rect
    exit_x: int
    exit_y: int
    direction: str = "down"
    label: str = ""


@dataclass
class Trigger:
    area: pygame.Rect
    event: str
    once: bool = True
    used: bool = False


@dataclass
class Level:
    name: str
    width: int = 120
    height: int = 17
    theme: str = "grass"
    time_limit: int = 300
    start: tuple[int, int] = (3 * TILE, 12 * TILE)
    tiles: list[TileCell] = field(default_factory=list)
    entities: list[EntitySpec] = field(default_factory=list)
    warps: list[Warp] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    layer_visible: dict[str, bool] = field(default_factory=lambda: {
        "background": True, "main": True, "secret": False, "foreground": True
    })
    event_scripts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    author: str = "CATSDK"

    def add_tile(self, x: int, y: int, kind: str, layer: str = "main",
                 payload: str = "coin") -> TileCell:
        cell = TileCell(x, y, kind, layer, True, 0.0, payload)
        self.tiles.append(cell)
        return cell

    def add_entity(self, kind: str, x: float, y: float,
                   layer: str = "main", **data: Any) -> EntitySpec:
        spec = EntitySpec(kind, x, y, layer, data)
        self.entities.append(spec)
        return spec

    def tile_at(self, gx: int, gy: int, layer: str | None = None) -> TileCell | None:
        for cell in reversed(self.tiles):
            if cell.active and cell.x == gx and cell.y == gy and (layer is None or cell.layer == layer):
                return cell
        return None

    def encode(self) -> str:
        payload = {
            "v": 1,
            "name": self.name,
            "size": [self.width, self.height],
            "theme": self.theme,
            "time": self.time_limit,
            "start": list(self.start),
            "tiles": [[t.x, t.y, t.kind, t.layer, t.payload] for t in self.tiles],
            "entities": [[e.kind, e.x, e.y, e.layer, e.data] for e in self.entities],
            "warps": [[w.entrance.x, w.entrance.y, w.entrance.w, w.entrance.h,
                       w.exit_x, w.exit_y, w.direction, w.label] for w in self.warps],
            "triggers": [[t.area.x, t.area.y, t.area.w, t.area.h, t.event, t.once]
                         for t in self.triggers],
            "events": self.event_scripts,
            "author": self.author,
        }
        packed = zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 9)
        return "UMBX39A:" + base64.urlsafe_b64encode(packed).decode()

    @classmethod
    def decode(cls, code: str) -> "Level":
        if not code.strip().startswith("UMBX39A:"):
            raise ValueError("Not an Ultra Mario Bros. X 39A level code")
        raw = base64.urlsafe_b64decode(code.strip().split(":", 1)[1])
        data = json.loads(zlib.decompress(raw))
        level = cls(
            str(data.get("name", "Imported Level")),
            int(data["size"][0]), int(data["size"][1]),
            str(data.get("theme", "grass")), int(data.get("time", 300)),
            tuple(data.get("start", [96, 384])), author=str(data.get("author", "Unknown")),
        )
        for item in data.get("tiles", []):
            level.add_tile(int(item[0]), int(item[1]), str(item[2]), str(item[3]), str(item[4]))
        for item in data.get("entities", []):
            level.add_entity(str(item[0]), float(item[1]), float(item[2]), str(item[3]), **dict(item[4]))
        for item in data.get("warps", []):
            level.warps.append(Warp(pygame.Rect(*map(int, item[:4])), int(item[4]), int(item[5]), str(item[6]), str(item[7])))
        for item in data.get("triggers", []):
            level.triggers.append(Trigger(pygame.Rect(*map(int, item[:4])), str(item[4]), bool(item[5])))
        level.event_scripts = dict(data.get("events", {}))
        return level


THEME_COLORS = {
    "grass": ((74, 163, 224), (190, 225, 255), (76, 174, 84)),
    "desert": ((247, 183, 91), (255, 225, 151), (208, 143, 55)),
    "cave": ((32, 36, 62), (69, 67, 94), (92, 79, 112)),
    "snow": ((96, 170, 220), (224, 244, 255), (179, 222, 239)),
    "night": ((18, 29, 70), (36, 55, 112), (59, 79, 135)),
    "factory": ((60, 65, 75), (100, 111, 123), (137, 73, 48)),
}


def hill(level: Level, start: int, width: int, top: int, kind: str = "ground") -> None:
    for gx in range(start, start + width):
        for gy in range(top, level.height):
            level.add_tile(gx, gy, kind)


def platform(level: Level, start: int, y: int, width: int, kind: str = "brick") -> None:
    for gx in range(start, start + width):
        level.add_tile(gx, y, kind)


def make_level(index: int) -> Level:
    themes = ["grass", "desert", "cave", "snow", "night", "factory"]
    names = [
        "1-1 Springboard Fields", "1-2 Amber Dunes", "1-3 Crystal Grotto",
        "2-1 Frostline Ridge", "2-2 Moonlit Canopy", "2-3 Gearheart Citadel",
    ]
    lengths = [116, 126, 132, 120, 136, 145]
    level = Level(names[index], lengths[index], 17, themes[index], 320 - index * 15)
    rng = random.Random(0x39A0 + index)
    # Segmented floor with designed gaps.
    gaps = {(17 + index, 2), (39 + index * 2, 3), (73 - index, 2), (96 + index, 3)}
    for gx in range(level.width):
        in_gap = any(start <= gx < start + size for start, size in gaps)
        if not in_gap:
            for gy in range(14, 17):
                level.add_tile(gx, gy, "ice" if index == 3 else "ground")
    for gx in range(7, level.width - 8, 11):
        y = rng.choice([9, 10, 11])
        platform(level, gx, y, rng.choice([3, 4, 5]), rng.choice(["brick", "cloud", "stone"]))
        if gx % 22 == 7:
            level.add_tile(gx + 1, y, "question", payload=rng.choice(["coin", "grow", "blaze", "leaf"]))
    # Terrain landmarks.
    hill(level, 26, 6, 12, "stone")
    hill(level, 56, 5, 11, "ice" if index == 3 else "ground")
    hill(level, level.width - 15, 9, 10, "stone")
    for gx in range(46, 51):
        level.add_tile(gx, 13, "spike")
    if index in (2, 5):
        for gx in range(67, 74):
            level.add_tile(gx, 13, "lava")
    if index == 2:
        for gx in range(82, 91):
            for gy in range(10, 14):
                level.add_tile(gx, gy, "water", "background")
    # Enemies and items.
    for gx in range(13, level.width - 18, 13):
        level.add_entity(rng.choice(["walker", "walker", "hopper", "flyer"]), gx * TILE, 12 * TILE)
    level.add_entity("checkpoint", (level.width // 2) * TILE, 12 * TILE)
    level.add_entity("spring", 36 * TILE, 13 * TILE)
    level.add_entity("platform", 64 * TILE, 10 * TILE, axis="x", distance=130, speed=65)
    level.add_entity("platform", 88 * TILE, 8 * TILE, axis="y", distance=110, speed=55)
    for gx in (10, 24, 43, 62, 80, 102):
        if gx < level.width - 4:
            level.add_entity("coin", gx * TILE, rng.choice([7, 8, 10]) * TILE)
    level.add_entity("shard", (level.width - 22) * TILE, 7 * TILE)
    # Warp pair and event-driven secret layer.
    level.warps.append(Warp(pygame.Rect(31 * TILE, 12 * TILE, 2 * TILE, 2 * TILE),
                            76 * TILE, 8 * TILE, "down", "A"))
    level.add_tile(31, 13, "stone")
    level.add_tile(32, 13, "stone")
    level.add_entity("switch", 52 * TILE, 10 * TILE, event="secret_on")
    for gx in range(54, 60):
        level.add_tile(gx, 8, "cloud", "secret")
    level.event_scripts["secret_on"] = [
        {"op": "show_layer", "layer": "secret"},
        {"op": "message", "text": "A hidden route appeared!"},
    ]
    if index == 5:
        level.add_entity("boss", (level.width - 11) * TILE, 11 * TILE, hp=6)
        level.add_entity("goal", (level.width - 3) * TILE, 10 * TILE, locked=True)
    else:
        level.add_entity("goal", (level.width - 5) * TILE, 10 * TILE)
    return level


def make_blank_level() -> Level:
    level = Level("Untitled 39A Level", 100, 17, "grass", 300)
    for gx in range(level.width):
        for gy in range(14, 17):
            level.add_tile(gx, gy, "ground")
    level.add_entity("goal", 92 * TILE, 10 * TILE)
    return level


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    color: tuple[int, int, int]
    size: int = 4

    def update(self, dt: float) -> None:
        self.life -= dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += 700 * dt


class Body:
    def __init__(self, kind: str, x: float, y: float, w: int = 28, h: int = 28,
                 layer: str = "main", **data: Any) -> None:
        self.kind, self.x, self.y = kind, float(x), float(y)
        self.w, self.h, self.layer = w, h, layer
        self.vx = self.vy = 0.0
        self.alive = True
        self.on_ground = False
        self.data = dict(data)
        self.timer = random.random() * 3.0
        self.home_x, self.home_y = self.x, self.y
        self.facing = -1

    @property
    def rect(self) -> pygame.Rect:
        return rect_from_floats(self.x, self.y, self.w, self.h)

    def move_solid(self, world: "PlaySession", dt: float, gravity: bool = True) -> None:
        if gravity:
            self.vy = min(MAX_FALL, self.vy + GRAVITY * dt)
        self.x += self.vx * dt
        rect = self.rect
        for tile in world.solids_near(rect):
            if rect.colliderect(tile.rect):
                if self.vx > 0:
                    self.x = tile.rect.left - self.w
                elif self.vx < 0:
                    self.x = tile.rect.right
                self.vx *= -1
                rect = self.rect
        old_bottom = self.rect.bottom
        self.y += self.vy * dt
        rect = self.rect
        self.on_ground = False
        for tile in world.solids_near(rect, include_one_way=True):
            definition = TILES[tile.kind]
            tr = tile.rect
            if definition.one_way and not (self.vy >= 0 and old_bottom <= tr.top + 7):
                continue
            if rect.colliderect(tr):
                if self.vy > 0:
                    self.y = tr.top - self.h
                    self.vy = 0
                    self.on_ground = True
                elif self.vy < 0 and not definition.one_way:
                    self.y = tr.bottom
                    self.vy = 0
                rect = self.rect


class Actor(Body):
    SIZES = {
        "walker": (28, 28), "hopper": (28, 30), "flyer": (30, 24),
        "coin": (18, 26), "shard": (22, 28), "grow": (28, 28),
        "blaze": (28, 28), "leaf": (28, 28), "checkpoint": (24, 64),
        "switch": (30, 20), "goal": (32, 128), "spring": (30, 18),
        "platform": (96, 18), "boss": (62, 62),
    }

    def __init__(self, spec: EntitySpec) -> None:
        w, h = self.SIZES.get(spec.kind, (28, 28))
        super().__init__(spec.kind, spec.x, spec.y, w, h, spec.layer, **spec.data)
        if self.kind in {"walker", "hopper", "boss"}:
            self.vx = float(self.data.get("speed", 70 if self.kind != "boss" else 95)) * -1
        if self.kind == "goal":
            self.y -= 96

    @property
    def harmful(self) -> bool:
        return self.kind in {"walker", "hopper", "flyer", "boss"}

    def update(self, world: "PlaySession", dt: float) -> None:
        self.timer += dt
        if not world.level.layer_visible.get(self.layer, True):
            return
        if self.kind == "walker":
            self.move_solid(world, dt)
        elif self.kind == "hopper":
            self.move_solid(world, dt)
            if self.on_ground and int(self.timer * 2) % 4 == 0:
                self.vy = -600
        elif self.kind == "flyer":
            self.x = self.home_x + math.sin(self.timer * 1.9) * 95
            self.y = self.home_y + math.sin(self.timer * 3.2) * 34
        elif self.kind in {"grow", "blaze", "leaf"}:
            if abs(self.vx) < 1:
                self.vx = 72
            self.move_solid(world, dt)
        elif self.kind == "platform":
            axis = str(self.data.get("axis", "x"))
            distance = float(self.data.get("distance", 100))
            speed = float(self.data.get("speed", 55))
            phase = math.sin(self.timer * speed / max(30.0, distance)) * distance
            old_x, old_y = self.x, self.y
            if axis == "x":
                self.x = self.home_x + phase
            else:
                self.y = self.home_y + phase
            dx, dy = self.x - old_x, self.y - old_y
            for player in world.players:
                if player.alive and abs(player.rect.bottom - self.rect.top) <= 6 and \
                        player.rect.right > self.rect.left and player.rect.left < self.rect.right:
                    player.x += dx
                    player.y += dy
                    player.on_ground = True
        elif self.kind == "boss":
            self.move_solid(world, dt)
            if self.on_ground and int(self.timer * 1.3) != int((self.timer - dt) * 1.3):
                self.vy = -520
                world.audio.play("boss", 0.7)
            self.data.setdefault("hp", 6)
        if self.y > world.level.height * TILE + 180:
            self.alive = False


class Projectile(Body):
    def __init__(self, x: float, y: float, direction: int, owner: int) -> None:
        super().__init__("fireball", x, y, 14, 14)
        self.vx = direction * 430
        self.vy = -100
        self.owner = owner
        self.bounces = 4
        self.life = 3.0

    def update(self, world: "PlaySession", dt: float) -> None:
        self.life -= dt
        old_vy = self.vy
        self.move_solid(world, dt)
        if self.on_ground and old_vy > 0:
            self.vy = -330
            self.bounces -= 1
        if self.bounces <= 0 or self.life <= 0:
            self.alive = False
        for actor in world.actors:
            if actor.alive and actor.harmful and self.rect.colliderect(actor.rect):
                world.hit_enemy(actor, 1)
                self.alive = False
                break


class Player(Body):
    def __init__(self, number: int, x: float, y: float, controls: Controls) -> None:
        super().__init__("player", x, y, 26, 30)
        self.number = number
        self.controls = controls
        self.power = "small"
        self.lives = 4
        self.coins = 0
        self.shards = 0
        self.score = 0
        self.reserve = ""
        self.active = number == 1
        self.invincible = 0.0
        self.star_time = 0.0
        self.coyote = 0.0
        self.jump_buffer = 0.0
        self.jump_held = False
        self.run_held = False
        self.shoot_cooldown = 0.0
        self.spin_time = 0.0
        self.ducking = False
        self.in_water = False
        self.climbing = False
        self.checkpoint = (x, y)
        self.combo = 0
        self.combo_time = 0.0

    def set_power(self, power: str) -> None:
        heights = {"small": 30, "big": 46, "blaze": 46, "leaf": 46}
        old_bottom = self.y + self.h
        self.power = power
        self.h = heights.get(power, 46)
        self.y = old_bottom - self.h

    def hurt(self, world: "PlaySession") -> None:
        if self.invincible > 0 or self.star_time > 0 or not self.alive:
            return
        world.audio.play("hurt")
        self.invincible = 1.8
        if self.power != "small":
            self.set_power("small")
            self.vy = -340
        else:
            self.die(world)

    def die(self, world: "PlaySession") -> None:
        if not self.alive:
            return
        self.alive = False
        self.lives -= 1
        self.vy = -650
        world.death_timer = max(world.death_timer, 1.6)
        for _ in range(18):
            world.particles.append(Particle(self.x + self.w / 2, self.y + self.h / 2,
                random.uniform(-190, 190), random.uniform(-300, -60), 0.7, (255, 220, 80), 5))

    def respawn(self) -> None:
        self.x, self.y = self.checkpoint
        self.vx = self.vy = 0
        self.alive = True
        self.invincible = 2.5
        self.set_power("small")

    def update(self, world: "PlaySession", dt: float, keys: pygame.key.ScancodeWrapper) -> None:
        if not self.active:
            return
        if not self.alive:
            self.y += self.vy * dt
            self.vy += GRAVITY * 0.55 * dt
            return
        self.invincible = max(0.0, self.invincible - dt)
        self.star_time = max(0.0, self.star_time - dt)
        self.shoot_cooldown = max(0.0, self.shoot_cooldown - dt)
        self.combo_time -= dt
        if self.combo_time <= 0:
            self.combo = 0
        self.coyote = 0.11 if self.on_ground else max(0.0, self.coyote - dt)
        self.jump_buffer = max(0.0, self.jump_buffer - dt)
        left, right = keys[self.controls.left], keys[self.controls.right]
        run = keys[self.controls.run]
        self.run_held = bool(run)
        direction = int(right) - int(left)
        self.ducking = bool(keys[self.controls.down] and self.on_ground and self.power != "small")
        self.climbing = any(t.kind == "vine" and self.rect.colliderect(t.rect)
                            for t in world.tiles_near(self.rect))
        self.in_water = any(t.kind == "water" and self.rect.colliderect(t.rect)
                            for t in world.tiles_near(self.rect))
        max_speed = 300 if run else 190
        if self.ducking:
            direction = 0
        accel = 1550 if self.on_ground else 930
        if direction:
            self.vx = approach(self.vx, direction * max_speed, accel * dt)
            self.facing = direction
        else:
            floor = world.tile_below(self.rect)
            friction = 260 if floor and floor.kind == "ice" else 1500
            self.vx = approach(self.vx, 0, friction * dt)
        if self.climbing and (keys[self.controls.up] or keys[self.controls.down]):
            self.vy = (int(keys[self.controls.down]) - int(keys[self.controls.up])) * 155
        elif self.in_water:
            self.vy = min(250, self.vy + GRAVITY * 0.18 * dt)
            self.vx *= 0.995
        else:
            self.vy = min(MAX_FALL, self.vy + GRAVITY * dt)
        if self.jump_buffer > 0 and (self.coyote > 0 or self.in_water or self.climbing):
            self.vy = -610 if run else -555
            if self.in_water:
                self.vy = -340
            self.on_ground = False
            self.coyote = self.jump_buffer = 0
            world.audio.play("jump")
        if not keys[self.controls.jump] and self.jump_held and self.vy < -180:
            self.vy *= 0.55
        self.jump_held = bool(keys[self.controls.jump])
        self._move_and_collide(world, dt)
        self._interact(world, keys)
        if self.y > world.level.height * TILE + 130:
            self.die(world)

    def press_jump(self) -> None:
        if self.active:
            self.jump_buffer = 0.12

    def press_alt(self) -> None:
        if self.active and self.power == "leaf" and not self.on_ground:
            self.spin_time = 0.35

    def _move_and_collide(self, world: "PlaySession", dt: float) -> None:
        self.x += self.vx * dt
        self.x = clamp(self.x, 0, world.level.width * TILE - self.w)
        rect = self.rect
        for tile in world.solids_near(rect):
            if rect.colliderect(tile.rect):
                if self.vx > 0:
                    self.x = tile.rect.left - self.w
                elif self.vx < 0:
                    self.x = tile.rect.right
                self.vx = 0
                rect = self.rect
        old_bottom = self.rect.bottom
        self.y += self.vy * dt
        rect = self.rect
        self.on_ground = False
        for tile in world.solids_near(rect, include_one_way=True):
            definition = TILES[tile.kind]
            tr = tile.rect
            if definition.one_way and not (self.vy >= 0 and old_bottom <= tr.top + 7 and not self.ducking):
                continue
            if rect.colliderect(tr):
                if self.vy > 0:
                    self.y = tr.top - self.h
                    self.vy = 0
                    self.on_ground = True
                elif self.vy < 0 and not definition.one_way:
                    self.y = tr.bottom
                    self.vy = 0
                    world.bump_tile(tile, self)
                rect = self.rect
        # Stand on moving platforms.
        for actor in world.actors:
            if actor.alive and actor.kind == "platform" and self.vy >= 0:
                if self.rect.colliderect(actor.rect) and old_bottom <= actor.rect.top + 8:
                    self.y = actor.rect.top - self.h
                    self.vy = 0
                    self.on_ground = True

    def _interact(self, world: "PlaySession", keys: pygame.key.ScancodeWrapper) -> None:
        for tile in world.tiles_near(self.rect.inflate(-8, -4)):
            if tile.active and TILES[tile.kind].hurt and self.rect.colliderect(tile.rect):
                self.hurt(world)
        for actor in world.actors:
            if not actor.alive or not world.level.layer_visible.get(actor.layer, True):
                continue
            if not self.rect.colliderect(actor.rect):
                continue
            if actor.kind == "coin":
                actor.alive = False
                self.coins += 1
                self.score += 100
                world.audio.play("coin")
                if self.coins >= 100:
                    self.coins -= 100
                    self.lives += 1
            elif actor.kind == "shard":
                actor.alive = False
                self.shards += 1
                self.score += 1000
                self.star_time = 4.0
                world.audio.play("power")
            elif actor.kind in {"grow", "blaze", "leaf"}:
                actor.alive = False
                incoming = "big" if actor.kind == "grow" else actor.kind
                if self.power != "small" and incoming != self.power:
                    self.reserve = actor.kind
                else:
                    self.set_power(incoming)
                self.score += 1000
                world.audio.play("power")
            elif actor.kind == "checkpoint":
                actor.data["active"] = True
                self.checkpoint = (actor.x - 24, actor.y)
            elif actor.kind == "switch" and not actor.data.get("used"):
                actor.data["used"] = True
                world.run_event(str(actor.data.get("event", "secret_on")))
                world.audio.play("click")
            elif actor.kind == "spring" and self.vy >= 0:
                self.y = actor.rect.top - self.h
                self.vy = -820
                world.audio.play("jump")
            elif actor.kind == "goal":
                if not actor.data.get("locked") or not any(a.alive and a.kind == "boss" for a in world.actors):
                    world.complete_level(self)
            elif actor.harmful:
                stomp = self.vy > 100 and self.rect.bottom - actor.rect.top < 18
                spin_hit = self.spin_time > 0
                if self.star_time > 0 or stomp or spin_hit:
                    damage = 2 if self.star_time > 0 else 1
                    world.hit_enemy(actor, damage)
                    if stomp:
                        self.vy = -410
                        self.combo += 1
                        self.combo_time = 1.2
                        self.score += min(8000, 100 * (2 ** min(self.combo, 6)))
                    world.audio.play("stomp")
                else:
                    self.hurt(world)


class PlaySession:
    def __init__(self, game: "Game", level: Level, index: int = 0,
                 editor_test: bool = False) -> None:
        self.game, self.audio, self.level = game, game.audio, level
        self.index, self.editor_test = index, editor_test
        sx, sy = level.start
        self.players = [Player(1, sx, sy, P1_KEYS), Player(2, sx + 28, sy, P2_KEYS)]
        self.actors = [Actor(spec) for spec in level.entities]
        self.projectiles: list[Projectile] = []
        self.particles: list[Particle] = []
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.elapsed = 0.0
        self.time_left = float(level.time_limit)
        self.paused = False
        self.pause_choice = 0
        self.message = ""
        self.message_time = 0.0
        self.clear_timer = 0.0
        self.death_timer = 0.0
        self.completed = False
        self.screen_shake = 0.0
        self.debug = False

    def tiles_near(self, rect: pygame.Rect) -> Iterable[TileCell]:
        pad = rect.inflate(TILE * 2, TILE * 2)
        for tile in self.level.tiles:
            if tile.active and self.level.layer_visible.get(tile.layer, True) and pad.colliderect(tile.rect):
                yield tile

    def solids_near(self, rect: pygame.Rect, include_one_way: bool = False) -> Iterable[TileCell]:
        for tile in self.tiles_near(rect):
            definition = TILES[tile.kind]
            if definition.solid or (include_one_way and definition.one_way):
                yield tile

    def tile_below(self, rect: pygame.Rect) -> TileCell | None:
        probe = pygame.Rect(rect.left + 4, rect.bottom, max(1, rect.w - 8), 4)
        return next((t for t in self.solids_near(probe, True) if probe.colliderect(t.rect)), None)

    def bump_tile(self, tile: TileCell, player: Player) -> None:
        definition = TILES[tile.kind]
        if not definition.bumpable:
            return
        tile.bump = 7
        if definition.breakable and player.power != "small":
            tile.active = False
            player.score += 50
            self.audio.play("break")
            for _ in range(8):
                self.particles.append(Particle(tile.rect.centerx, tile.rect.centery,
                    random.uniform(-180, 180), random.uniform(-340, -80), 0.65, definition.color, 6))
            return
        if tile.kind == "question":
            tile.kind = "used"
            if tile.payload == "coin":
                player.coins += 1
                player.score += 200
                self.audio.play("coin")
                self.particles.append(Particle(tile.rect.centerx, tile.rect.top,
                    0, -260, 0.55, (255, 224, 48), 8))
            else:
                self.actors.append(Actor(EntitySpec(tile.payload, tile.rect.x + 2, tile.rect.y - 30)))
                self.audio.play("power")
        for actor in self.actors:
            if actor.harmful and actor.rect.colliderect(tile.rect.move(0, -8)):
                self.hit_enemy(actor, 1)

    def run_event(self, name: str) -> None:
        for command in self.level.event_scripts.get(name, []):
            op = command.get("op")
            if op == "show_layer":
                self.level.layer_visible[str(command.get("layer", "secret"))] = True
            elif op == "hide_layer":
                self.level.layer_visible[str(command.get("layer", "secret"))] = False
            elif op == "toggle_layer":
                layer = str(command.get("layer", "secret"))
                self.level.layer_visible[layer] = not self.level.layer_visible.get(layer, True)
            elif op == "message":
                self.message = str(command.get("text", "Event triggered!"))
                self.message_time = 3.0
            elif op == "spawn":
                self.actors.append(Actor(EntitySpec(str(command.get("kind", "coin")),
                    float(command.get("x", 0)), float(command.get("y", 0)))))

    def hit_enemy(self, actor: Actor, damage: int) -> None:
        if actor.kind == "boss":
            actor.data["hp"] = int(actor.data.get("hp", 6)) - damage
            actor.vx *= -1
            self.screen_shake = 0.25
            if actor.data["hp"] <= 0:
                actor.alive = False
                for _ in range(34):
                    self.particles.append(Particle(actor.rect.centerx, actor.rect.centery,
                        random.uniform(-260, 260), random.uniform(-360, 80), 1.1,
                        random.choice([(255, 90, 50), (255, 220, 70), (210, 240, 255)]), 7))
        else:
            actor.alive = False
            actor.vy = -300
        self.screen_shake = max(self.screen_shake, 0.12)

    def complete_level(self, player: Player) -> None:
        if self.completed:
            return
        self.completed = True
        self.clear_timer = 2.4
        player.score += int(max(0, self.time_left)) * 10
        self.audio.play("goal")
        self.message = "COURSE CLEAR!"
        self.message_time = 3.0

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        key = event.key
        if key == pygame.K_ESCAPE:
            self.paused = not self.paused
            self.audio.play("click")
            return
        if self.paused:
            if key in (pygame.K_UP, pygame.K_w):
                self.pause_choice = (self.pause_choice - 1) % 3
            elif key in (pygame.K_DOWN, pygame.K_s):
                self.pause_choice = (self.pause_choice + 1) % 3
            elif key in (pygame.K_RETURN, pygame.K_z):
                if self.pause_choice == 0:
                    self.paused = False
                elif self.pause_choice == 1:
                    self.game.start_level(self.index, self.editor_test)
                else:
                    self.game.scene = Scene.EDITOR if self.editor_test else Scene.WORLD
            return
        if key == pygame.K_F3:
            self.debug = not self.debug
        if key == P1_KEYS.jump:
            self.players[0].press_jump()
        if key == P2_KEYS.jump:
            if not self.players[1].active:
                self.players[1].active = True
                self.players[1].x = self.players[0].x - 28
                self.message, self.message_time = "PLAYER 2 JOINED", 1.8
            self.players[1].press_jump()
        if key == P1_KEYS.alt:
            self.players[0].press_alt()
        if key == P2_KEYS.alt:
            self.players[1].press_alt()
        if key == pygame.K_q:
            for player in self.players:
                if player.active and player.reserve:
                    self.actors.append(Actor(EntitySpec(player.reserve, player.x, player.y - 40)))
                    player.reserve = ""

    def update(self, dt: float) -> None:
        if self.paused:
            return
        dt = min(dt, 1 / 20)
        self.elapsed += dt
        self.message_time = max(0.0, self.message_time - dt)
        self.screen_shake = max(0.0, self.screen_shake - dt)
        for tile in self.level.tiles:
            tile.bump = approach(tile.bump, 0, 45 * dt)
        if self.completed:
            self.clear_timer -= dt
            if self.clear_timer <= 0:
                if self.editor_test:
                    self.game.scene = Scene.EDITOR
                else:
                    self.game.finish_level(self.index)
            return
        self.time_left -= dt
        if self.time_left <= 0:
            self.time_left = 0
            for player in self.players:
                if player.active:
                    player.die(self)
        keys = pygame.key.get_pressed()
        for player in self.players:
            player.update(self, dt, keys)
            player.spin_time = max(0.0, player.spin_time - dt)
            if player.active and player.power == "blaze" and player.run_held and player.shoot_cooldown <= 0:
                if len([p for p in self.projectiles if p.alive and p.owner == player.number]) < 2:
                    self.projectiles.append(Projectile(player.x + player.w / 2, player.y + 14,
                                                       player.facing, player.number))
                    player.shoot_cooldown = 0.32
                    self.audio.play("fire", 0.65)
            if player.active and player.alive:
                for warp in self.level.warps:
                    if player.rect.colliderect(warp.entrance) and (
                        keys[player.controls.down] or keys[player.controls.up]
                    ):
                        player.x, player.y = warp.exit_x, warp.exit_y
                        player.vx = player.vy = 0
                        player.invincible = max(player.invincible, 0.5)
                        self.audio.play("warp")
                        self.message = f"WARP {warp.label}" if warp.label else "WARP"
                        self.message_time = 0.8
                        break
                for trigger in self.level.triggers:
                    if (not trigger.used or not trigger.once) and player.rect.colliderect(trigger.area):
                        self.run_event(trigger.event)
                        trigger.used = True
        for actor in self.actors:
            if actor.alive:
                actor.update(self, dt)
        for projectile in self.projectiles:
            if projectile.alive:
                projectile.update(self, dt)
        for particle in self.particles:
            particle.update(dt)
        self.actors = [a for a in self.actors if a.alive]
        self.projectiles = [p for p in self.projectiles if p.alive]
        self.particles = [p for p in self.particles if p.life > 0]
        active = [p for p in self.players if p.active and p.alive]
        if active:
            target_x = sum(p.rect.centerx for p in active) / len(active) - LOGICAL_W / 2
            target_y = sum(p.rect.centery for p in active) / len(active) - LOGICAL_H * 0.58
            self.camera_x += (clamp(target_x, 0, max(0, self.level.width * TILE - LOGICAL_W)) - self.camera_x) * min(1, dt * 5)
            self.camera_y += (clamp(target_y, -100, max(-100, self.level.height * TILE - LOGICAL_H)) - self.camera_y) * min(1, dt * 4)
        self.death_timer = max(0.0, self.death_timer - dt)
        if not active and self.death_timer <= 0:
            living = [p for p in self.players if p.active and p.lives >= 0]
            if living:
                for player in living:
                    player.respawn()
            else:
                self.game.scene = Scene.EDITOR if self.editor_test else Scene.WORLD

    def _screen_rect(self, rect: pygame.Rect) -> pygame.Rect:
        shake_x = random.randint(-5, 5) if self.screen_shake > 0 else 0
        shake_y = random.randint(-3, 3) if self.screen_shake > 0 else 0
        return rect.move(-round(self.camera_x) + shake_x, -round(self.camera_y) + shake_y)

    def draw(self, surface: pygame.Surface) -> None:
        draw_background(surface, self.level.theme, self.camera_x, self.camera_y, self.elapsed)
        # Draw background and main tiles, then actors/players, then foreground.
        for layer in ("background", "main", "secret"):
            if not self.level.layer_visible.get(layer, True):
                continue
            for tile in self.level.tiles:
                if tile.active and tile.layer == layer:
                    sr = self._screen_rect(tile.rect)
                    if sr.right >= -TILE and sr.left <= LOGICAL_W + TILE:
                        draw_tile(surface, tile, sr)
        for actor in self.actors:
            if actor.alive and actor.kind != "platform" and self.level.layer_visible.get(actor.layer, True):
                draw_actor(surface, actor, self._screen_rect(actor.rect), self.elapsed)
        for actor in self.actors:
            if actor.alive and actor.kind == "platform" and self.level.layer_visible.get(actor.layer, True):
                draw_actor(surface, actor, self._screen_rect(actor.rect), self.elapsed)
        for projectile in self.projectiles:
            sr = self._screen_rect(projectile.rect)
            pygame.draw.circle(surface, (255, 208, 65), sr.center, 7)
            pygame.draw.circle(surface, (255, 78, 32), sr.center, 7, 2)
        for player in self.players:
            if player.active and player.alive:
                if player.invincible <= 0 or int(player.invincible * 12) % 2 == 0:
                    draw_player(surface, player, self._screen_rect(player.rect), self.elapsed)
        for particle in self.particles:
            pygame.draw.rect(surface, particle.color,
                (round(particle.x - self.camera_x), round(particle.y - self.camera_y),
                 particle.size, particle.size))
        for tile in self.level.tiles:
            if tile.active and tile.layer == "foreground":
                draw_tile(surface, tile, self._screen_rect(tile.rect))
        self.draw_hud(surface)
        if self.debug:
            self.draw_debug(surface)
        if self.message_time > 0:
            panel = pygame.Rect(LOGICAL_W // 2 - 220, 88, 440, 54)
            pygame.draw.rect(surface, (10, 18, 40, 220), panel, border_radius=12)
            pygame.draw.rect(surface, CYAN, panel, 2, border_radius=12)
            text(surface, self.game.fonts[24], self.message, panel.center, CYAN, "center")
        if self.paused:
            self.draw_pause(surface)

    def draw_hud(self, surface: pygame.Surface) -> None:
        hud = pygame.Surface((LOGICAL_W, 58), pygame.SRCALPHA)
        hud.fill((8, 14, 30, 185))
        surface.blit(hud, (0, 0))
        p1 = self.players[0]
        text(surface, self.game.fonts[20], f"P1  ×{max(0, p1.lives)}", (18, 8), (255, 96, 92))
        text(surface, self.game.fonts[16], f"COIN {p1.coins:02d}   SCORE {p1.score:07d}", (18, 34))
        active_p2 = self.players[1].active
        if active_p2:
            p2 = self.players[1]
            text(surface, self.game.fonts[20], f"P2  ×{max(0, p2.lives)}", (250, 8), (92, 204, 255))
            text(surface, self.game.fonts[16], f"COIN {p2.coins:02d}   SCORE {p2.score:07d}", (250, 34))
        else:
            text(surface, self.game.fonts[16], "P2: PRESS F TO JOIN", (250, 20), (165, 182, 208))
        total_shards = sum(p.shards for p in self.players if p.active)
        text(surface, self.game.fonts[20], f"◆ {total_shards}/1", (705, 8), (255, 224, 75))
        text(surface, self.game.fonts[20], f"TIME {math.ceil(self.time_left):03d}", (826, 8), WHITE)
        text(surface, self.game.fonts[14], self.level.name, (LOGICAL_W - 16, 38), CYAN, "topright")

    def draw_pause(self, surface: pygame.Surface) -> None:
        overlay = pygame.Surface((LOGICAL_W, LOGICAL_H), pygame.SRCALPHA)
        overlay.fill((4, 7, 18, 175))
        surface.blit(overlay, (0, 0))
        panel = pygame.Rect(LOGICAL_W // 2 - 190, 130, 380, 280)
        pygame.draw.rect(surface, (22, 34, 65), panel, border_radius=18)
        pygame.draw.rect(surface, CYAN, panel, 3, border_radius=18)
        text(surface, self.game.fonts[38], "PAUSED", (LOGICAL_W // 2, 160), CYAN, "midtop")
        for i, label in enumerate(("RESUME", "RESTART", "EXIT COURSE")):
            box = pygame.Rect(panel.x + 45, 230 + i * 54, panel.w - 90, 42)
            if i == self.pause_choice:
                pygame.draw.rect(surface, (58, 106, 168), box, border_radius=9)
            text(surface, self.game.fonts[20], label, box.center,
                 WHITE if i == self.pause_choice else (160, 178, 205), "center")

    def draw_debug(self, surface: pygame.Surface) -> None:
        lines = [
            f"FPS {self.game.clock.get_fps():5.1f}",
            f"CAM {self.camera_x:.1f},{self.camera_y:.1f}",
            f"ACTORS {len(self.actors)}  TILES {sum(t.active for t in self.level.tiles)}",
            f"PROJECTILES {len(self.projectiles)}  PARTICLES {len(self.particles)}",
        ]
        for i, line in enumerate(lines):
            text(surface, self.game.fonts[14], line, (10, 70 + i * 17), (130, 255, 160), shadow=False)


def draw_background(surface: pygame.Surface, theme: str, camera_x: float,
                    camera_y: float, elapsed: float) -> None:
    top, bottom, accent = THEME_COLORS.get(theme, THEME_COLORS["grass"])
    for y in range(0, LOGICAL_H, 4):
        t = y / LOGICAL_H
        color = tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        pygame.draw.rect(surface, color, (0, y, LOGICAL_W, 4))
    if theme in {"night", "cave"}:
        rng = random.Random(390)
        for _ in range(75):
            x, y = rng.randrange(LOGICAL_W), rng.randrange(60, 390)
            glow = 150 + int(80 * math.sin(elapsed * 1.5 + x))
            pygame.draw.circle(surface, (glow, glow, min(255, glow + 40)), (x, y), 1)
    else:
        sun_x = 770 - int(camera_x * 0.03)
        pygame.draw.circle(surface, (255, 242, 167), (sun_x, 105), 42)
        pygame.draw.circle(surface, (255, 249, 210), (sun_x, 105), 31)
    # Three parallax hill layers.
    for depth, shade, base, amp in (
        (0.08, tuple(max(0, c - 55) for c in accent), 450, 55),
        (0.16, tuple(max(0, c - 30) for c in accent), 475, 40),
        (0.25, accent, 510, 30),
    ):
        points = [(-20, LOGICAL_H)]
        for x in range(-20, LOGICAL_W + 70, 55):
            wx = x + camera_x * depth
            y = base + math.sin(wx * 0.012) * amp + math.sin(wx * 0.028) * amp * 0.25 - camera_y * depth
            points.append((x, round(y)))
        points.append((LOGICAL_W + 20, LOGICAL_H))
        pygame.draw.polygon(surface, shade, points)


def draw_tile(surface: pygame.Surface, tile: TileCell, rect: pygame.Rect) -> None:
    kind = tile.kind
    definition = TILES.get(kind, TILES["stone"])
    if kind == "water":
        layer = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        layer.fill((*definition.color, 145))
        pygame.draw.line(layer, (170, 236, 255, 190), (0, 4), (rect.w, 4), 3)
        surface.blit(layer, rect)
        return
    if kind == "vine":
        pygame.draw.line(surface, definition.color, rect.midtop, rect.midbottom, 6)
        pygame.draw.ellipse(surface, (84, 210, 75), (rect.centerx, rect.y + 8, 12, 7))
        pygame.draw.ellipse(surface, (84, 210, 75), (rect.centerx - 12, rect.y + 19, 12, 7))
        return
    if kind == "decor":
        pygame.draw.circle(surface, definition.color, rect.midbottom, 14)
        pygame.draw.circle(surface, (105, 215, 103), (rect.centerx - 10, rect.centery), 10)
        return
    if kind == "spike":
        for i in range(3):
            x = rect.x + i * rect.w // 3
            pygame.draw.polygon(surface, definition.color,
                [(x, rect.bottom), (x + rect.w // 6, rect.top + 4), (x + rect.w // 3, rect.bottom)])
            pygame.draw.line(surface, (80, 92, 112), (x, rect.bottom - 1),
                             (x + rect.w // 6, rect.top + 4), 2)
        return
    if kind == "lava":
        pygame.draw.rect(surface, definition.color, rect)
        for i in range(3):
            pygame.draw.circle(surface, (255, 210, 45), (rect.x + 6 + i * 11, rect.y + 6 + (i % 2) * 5), 5)
        return
    color = definition.color
    pygame.draw.rect(surface, color, rect, border_radius=4 if kind in {"question", "used", "cloud"} else 0)
    hi = tuple(min(255, c + 38) for c in color)
    lo = tuple(max(0, c - 45) for c in color)
    pygame.draw.line(surface, hi, rect.topleft, rect.topright, 3)
    pygame.draw.line(surface, lo, rect.bottomleft, rect.bottomright, 4)
    pygame.draw.rect(surface, lo, rect, 2, border_radius=4 if kind in {"question", "used", "cloud"} else 0)
    if kind == "ground":
        pygame.draw.rect(surface, (87, 174, 70), (rect.x, rect.y, rect.w, 7))
        pygame.draw.line(surface, (139, 218, 96), (rect.x, rect.y + 2), (rect.right, rect.y + 2), 2)
        pygame.draw.circle(surface, lo, (rect.x + 8, rect.y + 18), 3)
        pygame.draw.circle(surface, lo, (rect.x + 24, rect.y + 27), 2)
    elif kind == "brick":
        pygame.draw.line(surface, lo, rect.midleft, rect.midright, 2)
        pygame.draw.line(surface, lo, (rect.centerx, rect.y), (rect.centerx, rect.centery), 2)
        pygame.draw.line(surface, lo, (rect.x + 8, rect.centery), (rect.x + 8, rect.bottom), 2)
    elif kind == "question":
        font = pygame.font.Font(None, 28)
        mark = font.render("?", True, (255, 249, 204))
        surface.blit(mark, mark.get_rect(center=rect.center))
    elif kind == "ice":
        pygame.draw.polygon(surface, (220, 251, 255),
            [(rect.x + 4, rect.y + 5), (rect.right - 4, rect.y + 5), (rect.x + 12, rect.bottom - 5)])
    elif kind == "cloud":
        pygame.draw.circle(surface, (255, 255, 255), (rect.x + 8, rect.centery), 9)
        pygame.draw.circle(surface, (255, 255, 255), (rect.centerx, rect.centery - 5), 12)
        pygame.draw.circle(surface, (255, 255, 255), (rect.right - 7, rect.centery), 8)


def draw_actor(surface: pygame.Surface, actor: Actor, rect: pygame.Rect, elapsed: float) -> None:
    kind = actor.kind
    if kind == "walker":
        pygame.draw.ellipse(surface, (156, 92, 55), rect)
        pygame.draw.ellipse(surface, (240, 211, 160), (rect.x + 4, rect.y + 12, rect.w - 8, rect.h - 9))
        pygame.draw.circle(surface, INK, (rect.x + 8, rect.y + 10), 2)
        pygame.draw.circle(surface, INK, (rect.right - 8, rect.y + 10), 2)
    elif kind == "hopper":
        pygame.draw.ellipse(surface, (63, 180, 82), rect)
        pygame.draw.circle(surface, WHITE, (rect.x + 8, rect.y + 9), 4)
        pygame.draw.circle(surface, WHITE, (rect.right - 8, rect.y + 9), 4)
        pygame.draw.line(surface, INK, (rect.x + 5, rect.bottom), (rect.x, rect.bottom + 4), 3)
        pygame.draw.line(surface, INK, (rect.right - 5, rect.bottom), (rect.right, rect.bottom + 4), 3)
    elif kind == "flyer":
        pygame.draw.ellipse(surface, (144, 86, 196), rect)
        wing = 5 + int(abs(math.sin(elapsed * 9)) * 7)
        pygame.draw.ellipse(surface, (228, 224, 255), (rect.x - wing, rect.y + 6, wing + 8, 10))
        pygame.draw.ellipse(surface, (228, 224, 255), (rect.right - 8, rect.y + 6, wing + 8, 10))
        pygame.draw.circle(surface, WHITE, (rect.centerx, rect.y + 8), 4)
    elif kind == "coin":
        width = max(4, int(rect.w * (0.35 + 0.65 * abs(math.sin(elapsed * 5 + actor.x)))))
        coin = pygame.Rect(0, 0, width, rect.h)
        coin.center = rect.center
        pygame.draw.ellipse(surface, (255, 217, 49), coin)
        pygame.draw.ellipse(surface, (255, 248, 156), coin, 2)
    elif kind == "shard":
        pygame.draw.polygon(surface, (255, 231, 67),
            [(rect.centerx, rect.top), (rect.right, rect.centery), (rect.centerx, rect.bottom), (rect.left, rect.centery)])
        pygame.draw.polygon(surface, (255, 255, 210),
            [(rect.centerx, rect.top + 4), (rect.centerx + 3, rect.centery), (rect.left + 4, rect.centery)], 0)
    elif kind in {"grow", "blaze", "leaf"}:
        colors = {"grow": (238, 72, 65), "blaze": (255, 180, 48), "leaf": (90, 202, 83)}
        pygame.draw.ellipse(surface, colors[kind], rect)
        pygame.draw.rect(surface, (248, 230, 180), (rect.x + 7, rect.centery, rect.w - 14, rect.h // 2))
        pygame.draw.circle(surface, WHITE, (rect.x + 7, rect.y + 8), 4)
        pygame.draw.circle(surface, WHITE, (rect.right - 7, rect.y + 8), 4)
    elif kind == "checkpoint":
        pygame.draw.rect(surface, (230, 236, 247), (rect.x + 4, rect.y, 5, rect.h))
        color = (255, 230, 70) if actor.data.get("active") else (97, 119, 151)
        pygame.draw.polygon(surface, color, [(rect.x + 9, rect.y + 6), (rect.right + 18, rect.y + 18), (rect.x + 9, rect.y + 30)])
    elif kind == "switch":
        pygame.draw.rect(surface, (71, 87, 118), rect, border_radius=4)
        top = rect.move(0, -5 if not actor.data.get("used") else 1)
        pygame.draw.rect(surface, (245, 75, 70), top, border_radius=7)
    elif kind == "goal":
        pygame.draw.rect(surface, (244, 246, 250), (rect.x + 5, rect.y, 5, rect.h))
        color = (120, 130, 145) if actor.data.get("locked") else (65, 232, 135)
        pygame.draw.polygon(surface, color, [(rect.x + 10, rect.y + 7), (rect.x + 43, rect.y + 19), (rect.x + 10, rect.y + 31)])
        pygame.draw.circle(surface, (255, 225, 70), (rect.x + 7, rect.y), 6)
    elif kind == "spring":
        pygame.draw.rect(surface, (230, 63, 70), rect, border_radius=4)
        pygame.draw.line(surface, WHITE, (rect.x + 4, rect.centery), (rect.right - 4, rect.centery), 3)
    elif kind == "platform":
        pygame.draw.rect(surface, (66, 76, 100), rect, border_radius=6)
        pygame.draw.rect(surface, (162, 184, 205), rect, 3, border_radius=6)
        for x in range(rect.x + 15, rect.right, 24):
            pygame.draw.circle(surface, (30, 38, 57), (x, rect.centery), 3)
    elif kind == "boss":
        pygame.draw.ellipse(surface, (170, 54, 59), rect)
        pygame.draw.polygon(surface, (245, 220, 118),
            [(rect.x + 8, rect.y + 15), (rect.x + 14, rect.y - 7), (rect.x + 24, rect.y + 13),
             (rect.centerx, rect.y - 9), (rect.x + 40, rect.y + 13), (rect.right - 9, rect.y - 5), (rect.right - 6, rect.y + 18)])
        pygame.draw.circle(surface, WHITE, (rect.x + 20, rect.y + 28), 7)
        pygame.draw.circle(surface, WHITE, (rect.right - 20, rect.y + 28), 7)
        pygame.draw.circle(surface, INK, (rect.x + 22, rect.y + 29), 3)
        pygame.draw.circle(surface, INK, (rect.right - 18, rect.y + 29), 3)
        hp = int(actor.data.get("hp", 6))
        pygame.draw.rect(surface, (25, 25, 38), (rect.x, rect.y - 19, rect.w, 8), border_radius=4)
        pygame.draw.rect(surface, (255, 77, 70), (rect.x + 2, rect.y - 17, max(0, (rect.w - 4) * hp // 6), 4), border_radius=2)


def draw_player(surface: pygame.Surface, player: Player, rect: pygame.Rect, elapsed: float) -> None:
    palettes = {
        1: ((231, 64, 63), (55, 107, 214), (248, 194, 137)),
        2: ((67, 183, 95), (98, 80, 194), (248, 194, 137)),
    }
    cap, suit, skin = palettes[player.number]
    if player.power == "blaze":
        cap, suit = (252, 245, 222), (242, 92, 45)
    elif player.power == "leaf":
        cap, suit = (88, 202, 80), (219, 155, 64)
    if player.star_time > 0:
        phase = int(elapsed * 10) % 3
        cap = [(255, 88, 88), (255, 235, 76), (94, 231, 255)][phase]
        suit = [(102, 255, 142), (185, 100, 255), (255, 126, 225)][phase]
    if player.ducking:
        body = pygame.Rect(rect.x, rect.bottom - 28, rect.w, 28)
    else:
        body = rect
    pygame.draw.rect(surface, suit, (body.x + 5, body.centery, body.w - 10, body.h // 2), border_radius=5)
    pygame.draw.ellipse(surface, skin, (body.x + 5, body.y + 4, body.w - 10, max(16, body.h // 2)))
    pygame.draw.rect(surface, cap, (body.x + 2, body.y, body.w - 4, 10), border_radius=5)
    brim_x = body.x if player.facing < 0 else body.right - 10
    pygame.draw.rect(surface, cap, (brim_x, body.y + 7, 10, 4))
    eye_x = body.centerx - 5 if player.facing < 0 else body.centerx + 5
    pygame.draw.circle(surface, INK, (eye_x, body.y + 13), 2)
    if player.power == "leaf":
        tail_x = body.right if player.facing < 0 else body.x - 13
        tail = pygame.Rect(tail_x, body.centery + int(math.sin(elapsed * 8) * 4), 15, 7)
        pygame.draw.ellipse(surface, (198, 133, 57), tail)
    pygame.draw.rect(surface, (52, 41, 42), (body.x + 2, body.bottom - 5, 10, 5), border_radius=2)
    pygame.draw.rect(surface, (52, 41, 42), (body.right - 12, body.bottom - 5, 10, 5), border_radius=2)


class Editor:
    CATEGORIES = {
        "TILES": list(TILES),
        "ACTORS": ["walker", "hopper", "flyer", "coin", "shard", "grow", "blaze", "leaf"],
        "OBJECTS": ["checkpoint", "switch", "goal", "spring", "platform", "boss"],
        "TOOLS": ["start", "warp", "trigger"],
    }
    LAYERS = ["background", "main", "secret", "foreground"]

    def __init__(self, game: "Game", level: Level | None = None) -> None:
        self.game = game
        self.level = level or make_blank_level()
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.category_index = 0
        self.selection_index = 0
        self.layer_index = 1
        self.grid = True
        self.show_help = True
        self.message = "Welcome to Build Mode — F5 tests your level"
        self.message_time = 4.0
        self.warp_start: tuple[int, int] | None = None
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self.drag_painting = False
        self.drag_erasing = False
        self.last_cell: tuple[int, int] | None = None
        self.clipboard_code = ""

    @property
    def category(self) -> str:
        return list(self.CATEGORIES)[self.category_index]

    @property
    def selection(self) -> str:
        choices = self.CATEGORIES[self.category]
        return choices[self.selection_index % len(choices)]

    @property
    def layer(self) -> str:
        return self.LAYERS[self.layer_index]

    def snapshot(self) -> None:
        try:
            self.undo_stack.append(self.level.encode())
            self.undo_stack = self.undo_stack[-40:]
            self.redo_stack.clear()
        except Exception:
            pass

    def undo(self) -> None:
        if not self.undo_stack:
            self.toast("Nothing to undo")
            return
        self.redo_stack.append(self.level.encode())
        self.level = Level.decode(self.undo_stack.pop())
        self.toast("Undo")

    def redo(self) -> None:
        if not self.redo_stack:
            self.toast("Nothing to redo")
            return
        self.undo_stack.append(self.level.encode())
        self.level = Level.decode(self.redo_stack.pop())
        self.toast("Redo")

    def toast(self, message: str, seconds: float = 2.0) -> None:
        self.message, self.message_time = message, seconds

    def copy_code(self) -> None:
        self.clipboard_code = self.level.encode()
        copied = False
        try:
            pygame.scrap.init()
            pygame.scrap.put(pygame.SCRAP_TEXT, self.clipboard_code.encode() + b"\x00")
            copied = True
        except pygame.error:
            pass
        self.toast("Level code copied" if copied else "Level code held in memory")

    def paste_code(self) -> None:
        code = self.clipboard_code
        try:
            pygame.scrap.init()
            raw = pygame.scrap.get(pygame.SCRAP_TEXT)
            if raw:
                code = raw.decode(errors="ignore").rstrip("\x00")
        except pygame.error:
            pass
        try:
            imported = Level.decode(code)
            self.snapshot()
            self.level = imported
            self.camera_x = self.camera_y = 0
            self.toast(f"Imported: {self.level.name}")
        except (ValueError, zlib.error, json.JSONDecodeError, base64.binascii.Error) as exc:
            self.toast(f"Import failed: {str(exc)[:40]}", 3.0)

    def screen_world(self, pos: tuple[int, int]) -> tuple[int, int]:
        return round(pos[0] + self.camera_x), round(pos[1] - 72 + self.camera_y)

    def cell_from_screen(self, pos: tuple[int, int]) -> tuple[int, int]:
        wx, wy = self.screen_world(pos)
        return math.floor(wx / TILE), math.floor(wy / TILE)

    def place(self, pos: tuple[int, int]) -> None:
        gx, gy = self.cell_from_screen(pos)
        if gx < 0 or gy < 0 or gx >= self.level.width or gy >= self.level.height:
            return
        if self.last_cell == (gx, gy):
            return
        self.last_cell = (gx, gy)
        selected = self.selection
        if self.category == "TILES":
            old = self.level.tile_at(gx, gy, self.layer)
            if old:
                old.kind = selected
            else:
                self.level.add_tile(gx, gy, selected, self.layer,
                    "grow" if selected == "question" else "coin")
        elif self.category in {"ACTORS", "OBJECTS"}:
            if not any(abs(e.x - gx * TILE) < 8 and abs(e.y - gy * TILE) < 8 for e in self.level.entities):
                data: dict[str, Any] = {}
                if selected == "switch":
                    data["event"] = "secret_on"
                    self.level.event_scripts.setdefault("secret_on", [
                        {"op": "toggle_layer", "layer": "secret"},
                        {"op": "message", "text": "Secret layer toggled!"},
                    ])
                if selected == "platform":
                    data.update(axis="x", distance=128, speed=55)
                if selected == "boss":
                    data["hp"] = 6
                self.level.add_entity(selected, gx * TILE, gy * TILE, self.layer, **data)
        elif selected == "start":
            self.level.start = (gx * TILE, gy * TILE)
            self.toast("Player start moved")
        elif selected == "warp":
            if self.warp_start is None:
                self.warp_start = (gx * TILE, gy * TILE)
                self.toast("Warp entrance set — choose its exit")
            else:
                x, y = self.warp_start
                self.level.warps.append(Warp(pygame.Rect(x, y, TILE, TILE), gx * TILE, gy * TILE, "down", str(len(self.level.warps) + 1)))
                self.warp_start = None
                self.toast("Warp linked")
        elif selected == "trigger":
            self.level.triggers.append(Trigger(pygame.Rect(gx * TILE, gy * TILE, TILE * 2, TILE * 2), "secret_on"))
            self.level.event_scripts.setdefault("secret_on", [{"op": "toggle_layer", "layer": "secret"}])
            self.toast("Event trigger placed")

    def erase(self, pos: tuple[int, int]) -> None:
        gx, gy = self.cell_from_screen(pos)
        if self.last_cell == (gx, gy):
            return
        self.last_cell = (gx, gy)
        if self.category == "TILES":
            cell = self.level.tile_at(gx, gy, self.layer)
            if cell:
                self.level.tiles.remove(cell)
        else:
            point = pygame.Vector2(gx * TILE + TILE / 2, gy * TILE + TILE / 2)
            options = [e for e in self.level.entities if pygame.Vector2(e.x + 14, e.y + 14).distance_to(point) < TILE]
            if options:
                self.level.entities.remove(options[-1])
            else:
                self.level.warps = [w for w in self.level.warps if not w.entrance.collidepoint(point)]

    def handle_event(self, event: pygame.event.Event) -> None:
        mods = pygame.key.get_mods()
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.game.scene = Scene.TITLE
            elif event.key == pygame.K_F5:
                self.game.start_editor_test()
            elif event.key == pygame.K_TAB:
                self.category_index = (self.category_index + (-1 if mods & pygame.KMOD_SHIFT else 1)) % len(self.CATEGORIES)
                self.selection_index = 0
                self.game.audio.play("click")
            elif pygame.K_1 <= event.key <= pygame.K_4:
                self.layer_index = event.key - pygame.K_1
                self.toast(f"Layer: {self.layer}")
            elif event.key == pygame.K_g:
                self.grid = not self.grid
            elif event.key == pygame.K_F1:
                self.show_help = not self.show_help
            elif event.key == pygame.K_z and mods & pygame.KMOD_CTRL:
                self.undo()
            elif event.key == pygame.K_y and mods & pygame.KMOD_CTRL:
                self.redo()
            elif event.key == pygame.K_c and mods & pygame.KMOD_CTRL:
                self.copy_code()
            elif event.key == pygame.K_v and mods & pygame.KMOD_CTRL:
                self.paste_code()
            elif event.key in (pygame.K_LEFTBRACKET, pygame.K_COMMA):
                self.selection_index = (self.selection_index - 1) % len(self.CATEGORIES[self.category])
            elif event.key in (pygame.K_RIGHTBRACKET, pygame.K_PERIOD):
                self.selection_index = (self.selection_index + 1) % len(self.CATEGORIES[self.category])
        elif event.type == pygame.MOUSEBUTTONDOWN and event.pos[1] >= 72:
            if event.button == 1:
                self.snapshot()
                self.drag_painting = True
                self.last_cell = None
                self.place(event.pos)
            elif event.button == 3:
                self.snapshot()
                self.drag_erasing = True
                self.last_cell = None
                self.erase(event.pos)
            elif event.button == 2:
                gx, gy = self.cell_from_screen(event.pos)
                cell = self.level.tile_at(gx, gy)
                if cell:
                    self.category_index = 0
                    self.selection_index = self.CATEGORIES["TILES"].index(cell.kind)
            elif event.button in (4, 5):
                choices = self.CATEGORIES[self.category]
                self.selection_index = (self.selection_index + (-1 if event.button == 4 else 1)) % len(choices)
        elif event.type == pygame.MOUSEBUTTONUP:
            self.drag_painting = self.drag_erasing = False
            self.last_cell = None
        elif event.type == pygame.MOUSEMOTION:
            if self.drag_painting:
                self.place(event.pos)
            elif self.drag_erasing:
                self.erase(event.pos)

    def update(self, dt: float) -> None:
        self.message_time = max(0.0, self.message_time - dt)
        keys = pygame.key.get_pressed()
        speed = 640 * (2 if keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT] else 1)
        self.camera_x += (int(keys[pygame.K_RIGHT] or keys[pygame.K_d]) - int(keys[pygame.K_LEFT] or keys[pygame.K_a])) * speed * dt
        self.camera_y += (int(keys[pygame.K_DOWN] or keys[pygame.K_s]) - int(keys[pygame.K_UP] or keys[pygame.K_w])) * speed * dt
        self.camera_x = clamp(self.camera_x, 0, max(0, self.level.width * TILE - LOGICAL_W))
        self.camera_y = clamp(self.camera_y, -72, max(-72, self.level.height * TILE - (LOGICAL_H - 72)))

    def draw(self, surface: pygame.Surface) -> None:
        draw_background(surface, self.level.theme, self.camera_x, self.camera_y, self.game.total_time)
        viewport = pygame.Rect(0, 72, LOGICAL_W, LOGICAL_H - 72)
        old_clip = surface.get_clip()
        surface.set_clip(viewport)
        offset_y = 72
        for tile in self.level.tiles:
            if not self.level.layer_visible.get(tile.layer, True) and tile.layer != self.layer:
                continue
            sr = tile.rect.move(-round(self.camera_x), offset_y - round(self.camera_y))
            if tile.layer != self.layer:
                ghost = pygame.Surface((TILE, TILE), pygame.SRCALPHA)
                ghost_tile = TileCell(0, 0, tile.kind)
                draw_tile(ghost, ghost_tile, pygame.Rect(0, 0, TILE, TILE))
                ghost.set_alpha(95)
                surface.blit(ghost, sr)
            else:
                draw_tile(surface, tile, sr)
        for spec in self.level.entities:
            actor = Actor(spec)
            sr = actor.rect.move(-round(self.camera_x), offset_y - round(self.camera_y))
            if spec.layer != self.layer:
                ghost = pygame.Surface((max(1, sr.w + 30), max(1, sr.h + 30)), pygame.SRCALPHA)
                draw_actor(ghost, actor, pygame.Rect(5, 5, actor.w, actor.h), self.game.total_time)
                ghost.set_alpha(100)
                surface.blit(ghost, (sr.x - 5, sr.y - 5))
            else:
                draw_actor(surface, actor, sr, self.game.total_time)
        for warp in self.level.warps:
            ent = warp.entrance.move(-round(self.camera_x), offset_y - round(self.camera_y))
            ex = round(warp.exit_x - self.camera_x)
            ey = round(warp.exit_y + offset_y - self.camera_y)
            pygame.draw.rect(surface, (220, 90, 255), ent, 3)
            pygame.draw.line(surface, (220, 90, 255), ent.center, (ex, ey), 2)
            pygame.draw.circle(surface, (220, 90, 255), (ex, ey), 7, 2)
        sx, sy = self.level.start
        start_rect = pygame.Rect(round(sx - self.camera_x), round(sy + offset_y - self.camera_y), 26, 30)
        dummy = Player(1, 0, 0, P1_KEYS)
        draw_player(surface, dummy, start_rect, self.game.total_time)
        if self.grid:
            for x in range(-int(self.camera_x) % TILE, LOGICAL_W, TILE):
                pygame.draw.line(surface, (255, 255, 255, 35), (x, 72), (x, LOGICAL_H))
            for y in range(72 - int(self.camera_y) % TILE, LOGICAL_H, TILE):
                pygame.draw.line(surface, (255, 255, 255, 35), (0, y), (LOGICAL_W, y))
        mouse = pygame.mouse.get_pos()
        logical = self.game.window_to_logical(mouse)
        if logical and logical[1] >= 72:
            gx, gy = self.cell_from_screen(logical)
            cursor = pygame.Rect(gx * TILE - round(self.camera_x), gy * TILE + offset_y - round(self.camera_y), TILE, TILE)
            pygame.draw.rect(surface, (255, 245, 92), cursor, 2)
        surface.set_clip(old_clip)
        self.draw_toolbar(surface)

    def draw_toolbar(self, surface: pygame.Surface) -> None:
        pygame.draw.rect(surface, (13, 22, 43), (0, 0, LOGICAL_W, 72))
        pygame.draw.line(surface, CYAN, (0, 70), (LOGICAL_W, 70), 2)
        text(surface, self.game.fonts[20], "BUILD MODE", (14, 8), CYAN)
        text(surface, self.game.fonts[14], f"{self.category} / {self.selection.upper()}", (14, 38))
        # Selected object preview.
        preview = pygame.Rect(236, 16, 38, 38)
        pygame.draw.rect(surface, (35, 53, 87), preview, border_radius=6)
        if self.category == "TILES":
            draw_tile(surface, TileCell(0, 0, self.selection), preview.inflate(-6, -6))
        elif self.category in {"ACTORS", "OBJECTS"}:
            draw_actor(surface, Actor(EntitySpec(self.selection, 0, 0)), preview.inflate(-7, -7), self.game.total_time)
        text(surface, self.game.fonts[14], f"LAYER {self.layer.upper()}  [1–4]", (300, 12), (173, 205, 241))
        text(surface, self.game.fonts[14], f"LEVEL {self.level.width}×{self.level.height}  TIME {self.level.time_limit}", (300, 37), (173, 205, 241))
        text(surface, self.game.fonts[16], "F5 TEST", (LOGICAL_W - 18, 12), (255, 230, 75), "topright")
        text(surface, self.game.fonts[13], "TAB CATEGORY  [ ] ITEM  F1 HELP", (LOGICAL_W - 18, 39), WHITE, "topright")
        if self.message_time > 0:
            box = pygame.Rect(240, LOGICAL_H - 42, 480, 30)
            pygame.draw.rect(surface, (10, 18, 38), box, border_radius=8)
            pygame.draw.rect(surface, CYAN, box, 1, border_radius=8)
            text(surface, self.game.fonts[14], self.message, box.center, WHITE, "center")
        if self.show_help:
            panel = pygame.Surface((285, 200), pygame.SRCALPHA)
            panel.fill((9, 15, 31, 220))
            surface.blit(panel, (12, 84))
            pygame.draw.rect(surface, CYAN, (12, 84, 285, 200), 2, border_radius=8)
            lines = [
                "EDITOR QUICK GUIDE", "Left drag: place", "Right drag: erase",
                "Middle: sample tile", "WASD/arrows: pan", "Shift: fast pan",
                "Ctrl+Z/Y: undo/redo", "Ctrl+C/V: share/import code",
                "G: grid   Esc: title", "F1: hide this panel",
            ]
            for i, line in enumerate(lines):
                text(surface, self.game.fonts[14 if i else 16], line, (24, 96 + i * 18),
                     CYAN if i == 0 else WHITE, shadow=False)


class Game:
    MENU_ITEMS = ["STORY MODE", "LEVEL EDITOR", "OPTIONS", "HELP", "CREDITS", "QUIT"]
    WORLD_POSITIONS = [(100, 375), (245, 305), (390, 375), (535, 265), (680, 345), (830, 245)]

    def __init__(self, headless: bool = False) -> None:
        pygame.init()
        pygame.font.init()
        self.headless = headless
        self.fullscreen = False
        self.integer_scale = False
        self.window_size = (1280, 720)
        flags = 0 if headless else pygame.RESIZABLE
        self.window = pygame.display.set_mode(self.window_size, flags)
        pygame.display.set_caption(f"{TITLE} — Python 3.14 / 60 FPS")
        self.canvas = pygame.Surface((LOGICAL_W, LOGICAL_H)).convert()
        self.clock = pygame.time.Clock()
        self.audio = Audio()
        self.fonts = {
            size: pygame.font.Font(None, size)
            for size in (13, 14, 15, 16, 18, 20, 22, 24, 28, 32, 38, 48, 64, 82)
        }
        self.scene = Scene.TITLE
        self.running = True
        self.menu_index = 0
        self.option_index = 0
        self.world_index = 0
        self.unlocked = 1
        self.completed: set[int] = set()
        self.session: PlaySession | None = None
        self.editor = Editor(self)
        self.total_time = 0.0
        self.title_anim = 0.0
        self.help_page = 0
        self.status = ""
        self.status_time = 0.0
        self.viewport = pygame.Rect(0, 0, *self.window_size)

    def reset_display(self) -> None:
        if self.headless:
            return
        flags = pygame.FULLSCREEN if self.fullscreen else pygame.RESIZABLE
        size = (0, 0) if self.fullscreen else self.window_size
        self.window = pygame.display.set_mode(size, flags)

    def window_to_logical(self, pos: tuple[int, int]) -> tuple[int, int] | None:
        if not self.viewport.collidepoint(pos):
            return None
        x = int((pos[0] - self.viewport.x) * LOGICAL_W / self.viewport.w)
        y = int((pos[1] - self.viewport.y) * LOGICAL_H / self.viewport.h)
        return x, y

    def translate_event(self, event: pygame.event.Event) -> pygame.event.Event | None:
        if hasattr(event, "pos"):
            logical = self.window_to_logical(event.pos)
            if logical is None:
                return None
            values = dict(event.dict)
            values["pos"] = logical
            if "rel" in values:
                values["rel"] = (
                    round(values["rel"][0] * LOGICAL_W / max(1, self.viewport.w)),
                    round(values["rel"][1] * LOGICAL_H / max(1, self.viewport.h)),
                )
            return pygame.event.Event(event.type, values)
        return event

    def start_level(self, index: int, editor_test: bool = False) -> None:
        if editor_test:
            level = Level.decode(self.editor.level.encode())
        else:
            level = make_level(index)
        self.session = PlaySession(self, level, index, editor_test)
        self.scene = Scene.PLAY

    def start_editor_test(self) -> None:
        self.start_level(0, True)

    def finish_level(self, index: int) -> None:
        self.completed.add(index)
        self.unlocked = max(self.unlocked, min(6, index + 2))
        self.world_index = min(index + 1, 5)
        self.scene = Scene.WORLD

    def handle_title(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_UP, pygame.K_w):
                self.menu_index = (self.menu_index - 1) % len(self.MENU_ITEMS)
                self.audio.play("click")
            elif event.key in (pygame.K_DOWN, pygame.K_s):
                self.menu_index = (self.menu_index + 1) % len(self.MENU_ITEMS)
                self.audio.play("click")
            elif event.key in (pygame.K_RETURN, pygame.K_z, pygame.K_SPACE):
                self.choose_title()
        elif event.type == pygame.MOUSEMOTION:
            for i, rect in enumerate(self.menu_rects()):
                if rect.collidepoint(event.pos):
                    self.menu_index = i
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, rect in enumerate(self.menu_rects()):
                if rect.collidepoint(event.pos):
                    self.menu_index = i
                    self.choose_title()

    def choose_title(self) -> None:
        self.audio.play("click")
        choice = self.MENU_ITEMS[self.menu_index]
        if choice == "STORY MODE":
            self.scene = Scene.WORLD
        elif choice == "LEVEL EDITOR":
            self.scene = Scene.EDITOR
        elif choice == "OPTIONS":
            self.scene = Scene.OPTIONS
        elif choice == "HELP":
            self.scene = Scene.HELP
        elif choice == "CREDITS":
            self.scene = Scene.CREDITS
        else:
            self.running = False

    def handle_world(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
                self.scene = Scene.TITLE
            elif event.key in (pygame.K_LEFT, pygame.K_a):
                self.world_index = max(0, self.world_index - 1)
                self.audio.play("click")
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self.world_index = min(self.unlocked - 1, self.world_index + 1)
                self.audio.play("click")
            elif event.key in (pygame.K_RETURN, pygame.K_z, pygame.K_SPACE):
                if self.world_index < self.unlocked:
                    self.start_level(self.world_index)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, pos in enumerate(self.WORLD_POSITIONS):
                if pygame.Vector2(event.pos).distance_to(pos) < 30 and i < self.unlocked:
                    if self.world_index == i:
                        self.start_level(i)
                    else:
                        self.world_index = i

    def handle_options(self, event: pygame.event.Event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_ESCAPE:
            self.scene = Scene.TITLE
        elif event.key in (pygame.K_UP, pygame.K_w):
            self.option_index = (self.option_index - 1) % 4
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.option_index = (self.option_index + 1) % 4
        elif event.key in (pygame.K_LEFT, pygame.K_a, pygame.K_RIGHT, pygame.K_d,
                           pygame.K_RETURN, pygame.K_z):
            direction = -1 if event.key in (pygame.K_LEFT, pygame.K_a) else 1
            if self.option_index == 0:
                self.audio.volume = clamp(self.audio.volume + direction * 0.1, 0, 1)
                self.audio.play("coin")
            elif self.option_index == 1:
                self.audio.enabled = not self.audio.enabled
                self.audio.play("click")
            elif self.option_index == 2:
                self.integer_scale = not self.integer_scale
            else:
                self.fullscreen = not self.fullscreen
                self.reset_display()

    def handle_simple_page(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_BACKSPACE, pygame.K_RETURN):
                self.scene = Scene.TITLE
            elif self.scene == Scene.HELP and event.key in (pygame.K_LEFT, pygame.K_a):
                self.help_page = (self.help_page - 1) % 3
            elif self.scene == Scene.HELP and event.key in (pygame.K_RIGHT, pygame.K_d):
                self.help_page = (self.help_page + 1) % 3

    def handle_global(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.QUIT:
            self.running = False
            return True
        if event.type == pygame.VIDEORESIZE and not self.fullscreen:
            self.window_size = (max(640, event.w), max(360, event.h))
        if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
            self.fullscreen = not self.fullscreen
            self.reset_display()
            return True
        return False

    def update(self, dt: float) -> None:
        self.total_time += dt
        self.title_anim += dt
        self.status_time = max(0.0, self.status_time - dt)
        if self.scene == Scene.PLAY and self.session:
            self.session.update(dt)
        elif self.scene == Scene.EDITOR:
            self.editor.update(dt)

    def menu_rects(self) -> list[pygame.Rect]:
        return [pygame.Rect(LOGICAL_W // 2 - 155, 260 + i * 40, 310, 33)
                for i in range(len(self.MENU_ITEMS))]

    def draw_title(self, surface: pygame.Surface) -> None:
        draw_background(surface, "night", self.title_anim * 12, 0, self.title_anim)
        # Decorative original platform scene.
        pygame.draw.rect(surface, (78, 161, 74), (0, 487, LOGICAL_W, 53))
        for x in range(0, LOGICAL_W, TILE):
            draw_tile(surface, TileCell(0, 0, "ground"), pygame.Rect(x, 487, TILE, TILE))
        logo_y = 58 + int(math.sin(self.title_anim * 2) * 5)
        text(surface, self.fonts[64], "ULTRA", (LOGICAL_W // 2, logo_y), (255, 227, 64), "midtop")
        text(surface, self.fonts[64], "MARIO BROS. X", (LOGICAL_W // 2, logo_y + 54), WHITE, "midtop")
        badge = pygame.Rect(LOGICAL_W // 2 - 76, logo_y + 116, 152, 38)
        pygame.draw.rect(surface, (224, 60, 69), badge, border_radius=12)
        pygame.draw.rect(surface, WHITE, badge, 2, border_radius=12)
        text(surface, self.fonts[28], "39A", badge.center, WHITE, "center")
        text(surface, self.fonts[14], "CLEAN-ROOM PYTHON EDITION • 60 FPS", (LOGICAL_W // 2, logo_y + 160), CYAN, "midtop")
        for i, (label, rect) in enumerate(zip(self.MENU_ITEMS, self.menu_rects())):
            selected = i == self.menu_index
            if selected:
                pulse = 45 + int(math.sin(self.title_anim * 5) * 12)
                pygame.draw.rect(surface, (35 + pulse // 3, 67 + pulse // 3, 120 + pulse), rect, border_radius=8)
                pygame.draw.rect(surface, CYAN, rect, 2, border_radius=8)
                text(surface, self.fonts[18], "▶", (rect.x + 16, rect.centery), (255, 235, 80), "midleft")
            text(surface, self.fonts[20], label, rect.center,
                 WHITE if selected else (171, 189, 216), "center")
        text(surface, self.fonts[13], "Python 3.14+ • pygame-ce • No external assets", (14, LOGICAL_H - 22), (170, 185, 212))
        text(surface, self.fonts[13], f"VERSION {VERSION}", (LOGICAL_W - 14, LOGICAL_H - 22), (170, 185, 212), "topright")

    def draw_world(self, surface: pygame.Surface) -> None:
        draw_background(surface, "grass", self.total_time * 4, -20, self.total_time)
        text(surface, self.fonts[38], "ADVENTURE MAP", (LOGICAL_W // 2, 24), WHITE, "midtop")
        text(surface, self.fonts[16], "Choose a course • Z/Enter to play • Esc to title", (LOGICAL_W // 2, 68), CYAN, "midtop")
        # Winding road.
        for a, b in zip(self.WORLD_POSITIONS, self.WORLD_POSITIONS[1:]):
            pygame.draw.line(surface, (231, 212, 158), a, b, 18)
            pygame.draw.line(surface, (121, 98, 76), a, b, 2)
        names = ["FIELDS", "DUNES", "GROTTO", "RIDGE", "CANOPY", "CITADEL"]
        for i, pos in enumerate(self.WORLD_POSITIONS):
            unlocked = i < self.unlocked
            done = i in self.completed
            color = (71, 218, 119) if done else ((255, 218, 67) if unlocked else (83, 94, 114))
            pygame.draw.circle(surface, (18, 29, 52), pos, 32)
            pygame.draw.circle(surface, color, pos, 26)
            pygame.draw.circle(surface, WHITE if i == self.world_index else (30, 38, 58), pos, 29, 3)
            text(surface, self.fonts[20], "✓" if done else str(i + 1), pos, INK if unlocked else (150, 155, 165), "center", False)
            text(surface, self.fonts[14], names[i], (pos[0], pos[1] + 38), WHITE if unlocked else (120, 130, 145), "midtop")
        selected_level = make_level(self.world_index)
        panel = pygame.Rect(205, 440, 550, 72)
        pygame.draw.rect(surface, (12, 23, 46), panel, border_radius=13)
        pygame.draw.rect(surface, CYAN, panel, 2, border_radius=13)
        text(surface, self.fonts[22], selected_level.name, (panel.centerx, panel.y + 10), WHITE, "midtop")
        state = "COMPLETE" if self.world_index in self.completed else ("READY" if self.world_index < self.unlocked else "LOCKED")
        text(surface, self.fonts[15], f"{selected_level.theme.upper()} THEME  •  {selected_level.time_limit}s  •  {state}",
             (panel.centerx, panel.y + 40), (255, 226, 75) if state != "LOCKED" else (145, 153, 170), "midtop")

    def draw_options(self, surface: pygame.Surface) -> None:
        surface.fill((18, 29, 58))
        text(surface, self.fonts[48], "OPTIONS", (LOGICAL_W // 2, 62), CYAN, "midtop")
        values = [f"{round(self.audio.volume * 10) * 10}%", "ON" if self.audio.enabled else "OFF",
                  "ON" if self.integer_scale else "OFF", "ON" if self.fullscreen else "OFF"]
        labels = ["SOUND VOLUME", "SOUND EFFECTS", "INTEGER SCALING", "FULLSCREEN"]
        for i, (label, value) in enumerate(zip(labels, values)):
            rect = pygame.Rect(240, 165 + i * 70, 480, 52)
            pygame.draw.rect(surface, (48, 78, 125) if i == self.option_index else (27, 44, 79), rect, border_radius=10)
            if i == self.option_index:
                pygame.draw.rect(surface, CYAN, rect, 2, border_radius=10)
            text(surface, self.fonts[20], label, (rect.x + 20, rect.centery), WHITE, "midleft")
            text(surface, self.fonts[20], value, (rect.right - 20, rect.centery), (255, 229, 82), "midright")
        text(surface, self.fonts[15], "Arrow keys change values • F11 toggles fullscreen anywhere • Esc returns", (LOGICAL_W // 2, 480), (175, 195, 220), "midtop")

    def draw_help(self, surface: pygame.Surface) -> None:
        surface.fill((14, 24, 48))
        pages = [
            ("PLAY CONTROLS", [
                "Player 1: Arrow keys move/climb/duck", "Z jumps or swims • X runs and uses Blaze power",
                "C spins while using Leaf power • Q drops reserve item", "Player 2: WASD move • F jump • G run • H spin",
                "F joins as Player 2 • Esc pauses • F3 shows debug data", "Enter doors and warps with Up or Down",
            ]),
            ("BUILD MODE", [
                "Left-click paints • Right-click erases • Middle-click samples", "Tab changes category • [ and ] change object",
                "Keys 1–4 choose background/main/secret/foreground", "WASD or arrows pan • Shift pans faster • G toggles grid",
                "Ctrl+Z/Y undo and redo • F5 instantly test-plays", "Ctrl+C/V exports and imports compressed level codes",
            ]),
            ("ENGINE FEATURES", [
                "Six courses, world map, checkpoints, warps and local co-op", "Four power states, reserve items, water, vines and ice",
                "Layer events, switches, triggers and moving platforms", "Enemies, projectiles, a multi-hit boss and combo scoring",
                "Procedural art/audio, widescreen scaling and 60 FPS timing", "FILES_OFF: game content lives entirely in this Python file",
            ]),
        ]
        heading, lines = pages[self.help_page]
        text(surface, self.fonts[48], heading, (LOGICAL_W // 2, 55), CYAN, "midtop")
        panel = pygame.Rect(120, 140, 720, 285)
        pygame.draw.rect(surface, (25, 41, 74), panel, border_radius=15)
        pygame.draw.rect(surface, (85, 145, 210), panel, 2, border_radius=15)
        for i, line in enumerate(lines):
            text(surface, self.fonts[18], "◆", (150, 171 + i * 38), (255, 226, 74), "midleft")
            text(surface, self.fonts[18], line, (180, 171 + i * 38), WHITE, "midleft")
        text(surface, self.fonts[18], f"◀  PAGE {self.help_page + 1}/3  ▶", (LOGICAL_W // 2, 452), CYAN, "midtop")
        text(surface, self.fonts[14], "Left/Right changes page • Enter/Esc returns", (LOGICAL_W // 2, 489), (170, 190, 217), "midtop")

    def draw_credits(self, surface: pygame.Surface) -> None:
        draw_background(surface, "night", 0, 0, self.total_time)
        text(surface, self.fonts[48], "CREDITS", (LOGICAL_W // 2, 60), CYAN, "midtop")
        lines = [
            ("DESIGN & DIRECTION", "AC Kondo / CATSDK"),
            ("ENGINE", "Clean-room Python + pygame-ce"),
            ("GRAPHICS & AUDIO", "Procedurally generated at runtime"),
            ("EDITION", "Ultra Mario Bros. X 39A"),
        ]
        for i, (role, credit) in enumerate(lines):
            y = 155 + i * 68
            text(surface, self.fonts[15], role, (LOGICAL_W // 2, y), (255, 225, 78), "midtop")
            text(surface, self.fonts[22], credit, (LOGICAL_W // 2, y + 23), WHITE, "midtop")
        text(surface, self.fonts[14], "An original fan-development experiment. No extracted game assets or proprietary source code included.",
             (LOGICAL_W // 2, 455), (172, 191, 216), "midtop")
        text(surface, self.fonts[16], "Enter or Esc to return", (LOGICAL_W // 2, 493), CYAN, "midtop")

    def draw(self) -> None:
        if self.scene == Scene.TITLE:
            self.draw_title(self.canvas)
        elif self.scene == Scene.WORLD:
            self.draw_world(self.canvas)
        elif self.scene == Scene.PLAY and self.session:
            self.session.draw(self.canvas)
        elif self.scene == Scene.EDITOR:
            self.editor.draw(self.canvas)
        elif self.scene == Scene.OPTIONS:
            self.draw_options(self.canvas)
        elif self.scene == Scene.HELP:
            self.draw_help(self.canvas)
        elif self.scene == Scene.CREDITS:
            self.draw_credits(self.canvas)
        self.present()

    def present(self) -> None:
        window_w, window_h = self.window.get_size()
        scale = min(window_w / LOGICAL_W, window_h / LOGICAL_H)
        if self.integer_scale and scale >= 1:
            scale = max(1, int(scale))
        width, height = max(1, round(LOGICAL_W * scale)), max(1, round(LOGICAL_H * scale))
        self.viewport = pygame.Rect((window_w - width) // 2, (window_h - height) // 2, width, height)
        self.window.fill((3, 5, 12))
        smooth = not self.integer_scale and (width, height) != (LOGICAL_W, LOGICAL_H)
        image = pygame.transform.smoothscale(self.canvas, (width, height)) if smooth else pygame.transform.scale(self.canvas, (width, height))
        self.window.blit(image, self.viewport)
        if not self.headless:
            pygame.display.flip()

    def run(self) -> None:
        while self.running:
            dt = min(self.clock.tick(FPS) / 1000.0, 0.05)
            for raw_event in pygame.event.get():
                if self.handle_global(raw_event):
                    continue
                event = self.translate_event(raw_event)
                if event is None:
                    continue
                if self.scene == Scene.TITLE:
                    self.handle_title(event)
                elif self.scene == Scene.WORLD:
                    self.handle_world(event)
                elif self.scene == Scene.PLAY and self.session:
                    self.session.handle_event(event)
                elif self.scene == Scene.EDITOR:
                    self.editor.handle_event(event)
                elif self.scene == Scene.OPTIONS:
                    self.handle_options(event)
                elif self.scene in (Scene.HELP, Scene.CREDITS):
                    self.handle_simple_page(event)
            self.update(dt)
            self.draw()
        pygame.quit()


def self_test() -> int:
    """Fast headless integration check used by release builds."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    game = Game(headless=True)
    assertions = 0
    for index in range(6):
        level = make_level(index)
        assert level.tiles and level.entities
        encoded = level.encode()
        clone = Level.decode(encoded)
        assert clone.name == level.name and len(clone.tiles) == len(level.tiles)
        assertions += 2
    game.start_level(0)
    assert game.session is not None
    for _ in range(8):
        game.session.update(1 / 60)
    game.session.draw(game.canvas)
    assertions += 2
    game.scene = Scene.EDITOR
    game.editor.draw(game.canvas)
    assertions += 1
    pixel_sum = sum(game.canvas.get_at((x, LOGICAL_H // 2)).r for x in range(0, LOGICAL_W, 80))
    assert pixel_sum > 0
    assertions += 1
    pygame.quit()
    print(f"{TITLE} self-test passed: {assertions} checks")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{TITLE} — clean-room platform engine and editor")
    parser.add_argument("--self-test", action="store_true", help="run headless engine checks and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.14+ is recommended (Python 3.11 is the minimum fallback).")
    Game().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
