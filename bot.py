#!/usr/bin/env python3

import time, random, math

import pigpio
import evdev
from evdev import ecodes
from PIL import Image, ImageDraw, ImageFilter, ImageMath, ImageOps


class Motors:
    def __init__(self, pi, hz=8000):
        self.pi = pi
        self.pins = (23, 24, 25)
        self.count = len(self.pins)
        for pin in self.pins:
            self.pi.set_PWM_frequency(pin, hz)
        self.off()

    def set(self, speeds):
        self.speeds = tuple(speeds)
        for speed, pin in zip(self.speeds, self.pins):
            self.pi.set_PWM_dutycycle(pin, max(0, min(255, int(255 * speed))))

    def off(self):
        self.set((0,) * self.count)


class TabletTx:
    def __init__(self, pi):
        self.pi = pi
        self.set_idle()

    def set_idle(self):
        self.set_hz(255000)

    def set_hz(self, hz, duty=0.1):
        self.pi.hardware_PWM(18, hz, int(1e6 * duty))


class TabletRx:
    def __init__(self):
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_ABS in caps:
                absolute = dict(caps[ecodes.EV_ABS])
                if ecodes.ABS_X in absolute and ecodes.ABS_Y in absolute:
                    self.x = absolute[ecodes.ABS_X]
                    self.y = absolute[ecodes.ABS_Y]
                    self.dev = dev
                    return
        raise IOError("No suitable tablet device found")

    def poll(self):
        while True:
            event = self.dev.read_one()
            if not event:
                break
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_X:
                    self.x = self.x._replace(value=event.value)
                if event.code == ecodes.ABS_Y:
                    self.y = self.y._replace(value=event.value)

    def scaled_pos(self):
        x_size = self.x.max - self.x.min
        y_size = self.y.max - self.y.min
        scale = min(1.0 / x_size, 1.0 / y_size)
        return ((self.x.value - self.x.min) * scale, (self.y.value - self.y.min) * scale)


class WiggleMode:
    def __init__(self, pwm):
        self.pwm = pwm
        self.velocity = None
        self.last_frame_counter = None


class WiggleBot:
    pwm_initial = 0.8
    pwm_acceleration = 1.01

    def __init__(self):
        self.pi = pigpio.pi()
        self.tablet_tx = TabletTx(self.pi)
        self.tablet_rx = TabletRx()
        self.motors = Motors(self.pi)

        self.position = None
        self.velocity = None
        self.frame_counter = 0
        self.vibration_modes = [WiggleMode([
            self.pwm_initial * (mode_id == motor_id)
            for motor_id in range(self.motors.count)
        ]) for mode_id in range(self.motors.count)]
        self.current_mode = random.randrange(0, self.motors.count)

    def update(self):
        self.frame_counter += 1
        self.tablet_rx.poll()
        position = self.tablet_rx.scaled_pos()
        if self.position:
            self.velocity = (position[0] - self.position[0], position[1] - self.position[1])
            m = self.vibration_modes[self.current_mode]
            m.velocity = self.velocity
            m.last_frame_counter = self.frame_counter
        self.position = position

    def accelerate(self):
        self.motors.set([min(1.0, s * self.pwm_acceleration) for s in self.motors.speeds])

    def change_mode(self, mode):
        self.current_mode = mode
        self.motors.set(self.vibration_modes[self.current_mode].pwm)


class GreatArtist:
    def __init__(self, bot, inspiration):
        self.bot = bot
        self.inspiration = ImageOps.invert(Image.open(inspiration).convert('L'))
        self.progress = Image.new('L', self.inspiration.size, 0)
        self.draw = ImageDraw.Draw(self.progress)
        self.goal = None
        self.mode_scores = None
        self.coarse_kernel = ImageFilter.GaussianBlur(64)
        self.fine_kernel = ImageFilter.GaussianBlur(4)

    def step(self):
        self.bot.update()

        if self.bot.velocity and self.goal:
            next_mode = self.choose_mode()
            if next_mode == self.bot.current_mode:
                self.bot.accelerate()
            else:
                self.bot.change_mode(next_mode)

        self.status()
        self.update_goal()
        time.sleep(0.15)

    def choose_mode(self):
        scores = list(map(self.evaluate_vibration_mode, range(len(self.bot.vibration_modes))))
        self.mode_scores = scores
        best_mode = 0
        for mode, score in enumerate(scores):
            if score > scores[best_mode]:
                best_mode = mode
        return best_mode

    def status(self):
        velocities= [m.velocity for m in self.bot.vibration_modes]
        print("Mode %d, mode_scores=%r velocities=%r" % (self.bot.current_mode, self.mode_scores, velocities))

    def update_goal(self):
        self.goal = self.inspiration

        # next_pos = tablet_rx.scaled_pos(self.inspiration.size)
        # if self.pen_position:
        #     self.draw.line(self.pen_position + next_pos, fill=255, width=1)
        #     self.pen_velocity = (next_pos[0] - self.pen_position[0], next_pos[1] - self.pen_position[1])

        # sub = ImageMath.eval("convert(a-b*2/3, 'L')", dict(a=self.inspiration, b=self.progress))
        # coarse = sub.filter(self.coarse_kernel).filter(self.coarse_kernel)
        # self.eye = ImageMath.eval("convert((a+b)/2, 'L')", dict(a=sub, b=coarse)).filter(self.fine_kernel)

        # self.eye.save("out/eye-%06d.png" % self.counter)
        # self.progress.save("out/prog-%06d.png" % self.counter)
        # self.counter = self.counter + 1
        # self.pen_position = next_pos

    # def should_change_direction(self, threshold=0.6):

    #     if not self.pen_velocity:
    #         return False

    #     current_score = self.evaluate_ray(self.pen_velocity)
    #     sampled_scores = self.evaluate_random_rays()
    #     print("current direction: %r   others: %r" % (current_score, sampled_scores))

    #     return current_score < sampled_scores[int(len(sampled_scores) * threshold)]

    # def evaluate_random_rays(self, count=16):
    #     pen_speed = math.sqrt(math.pow(self.pen_velocity[0], 2) + math.pow(self.pen_velocity[1], 2))
    #     jitter = random.uniform(0, math.pi*2.0)
    #     rays = []
    #     for i in range(count):
    #         angle = i * math.pi * 2.0 / (count - 1.0)
    #         vec = (pen_speed * math.cos(angle), pen_speed * math.sin(angle))
    #         rays.append(self.evaluate_ray(vec))
    #     rays.sort()
    #     return rays

    def _sample_goal_int(self, pos, border):
        if pos[0] < 0 or pos[0] > self.goal.size[0]-1 or pos[1] < 0 or pos[1] > self.goal.size[1]-1:
            return border
        print(pos)
        return self.goal.getpixel(pos)

    def _sample_goal_bilinear(self, pos, border):
        ipos = (int(pos[0]), int(pos[1]))
        fpos = (pos[0] - ipos[0], pos[1] - ipos[1])
        s00 = self._sample_goal_int((ipos[0]  , ipos[1]  ), border)
        s10 = self._sample_goal_int((ipos[0]+1, ipos[1]  ), border)
        s01 = self._sample_goal_int((ipos[0]  , ipos[1]+1), border)
        s11 = self._sample_goal_int((ipos[0]+1, ipos[1]+1), border)
        sx0 = fpos[0] * s10 + (1.0 - fpos[0]) * s00
        sx1 = fpos[0] * s11 + (1.0 - fpos[0]) * s01
        return fpos[1] * sx0 + (1.0 - fpos[1]) * sx1

    def sample_goal(self, pos, edge_penalty=-100.0):
        to_pixels = max(*self.goal.size)
        scaled = (pos[0] * to_pixels, pos[1] * to_pixels)
        return self._sample_goal_bilinear(scaled, border=edge_penalty)

    def evaluate_ray(self, vec, step_length=0.02, weight_step=0.5, weight_min=0.001):
        """Score a ray starting at the current location, with the given per-frame velocity"""

        pos = self.bot.position
        total = 0
        weight = 1.0

        if not vec:
            return 0
        vec_len = math.sqrt(math.pow(vec[0], 2) + math.pow(vec[1], 2))
        if vec_len <= 0:
            return 0
        step_vec = (vec[0] * step_length / vec_len, vec[1] * step_length / vec_len)

        while weight > weight_min:
            pos = (pos[0] + step_vec[0], pos[1] + step_vec[1])
            total += self.sample_goal(pos) * weight
            weight = weight * weight_step

        return total

    def evaluate_vibration_mode(self, index, age_modifier=1.0):
        mode = self.bot.vibration_modes[index]
        age = self.bot.frame_counter - (mode.last_frame_counter or 0)
        score = self.evaluate_ray(mode.velocity)
        return score + age_modifier * age


def main():
    a = GreatArtist(WiggleBot(), "images/rng2.png")
    try:
        while True:
            a.step()
    finally:
        a.bot.motors.off()

if __name__ == "__main__":
    main()

