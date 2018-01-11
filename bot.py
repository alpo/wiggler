#!/usr/bin/env python3

import time, random, math, sys

import pigpio
import evdev
from evdev import ecodes
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageMath, ImageOps


class Motors:
    def __init__(self, pi, hz=200):
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
    pwm_initial = 0.6
    pwm_acceleration = 1.05

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
        self.change_mode(random.randrange(0, self.motors.count))

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
        self.output_frame_count = 0
        self.font = ImageFont.truetype('DroidSansMono.ttf', 10)
        self.inspiration = ImageOps.invert(Image.open(inspiration).convert('L'))
        self.progress = Image.new('L', self.inspiration.size, 0)
        self.debugview = Image.new('L', self.inspiration.size, 0)
        self.goal = None
        self.mode_scores = None
        self.step_timestamp = None
        self.large_blur = ImageFilter.GaussianBlur(max(*self.inspiration.size)/3)

    def step(self, goal_update_rate=40, min_step_duration=1/15, mode_change_delay=1/5):
        prev_position = self.bot.position
        self.bot.update()
        self.record_bot_travel(prev_position, self.bot.position)

        step_duration = min_step_duration
        if self.bot.velocity and self.goal:
            next_mode = self.choose_mode()
            if next_mode == self.bot.current_mode:
                self.bot.accelerate()
            else:
                self.bot.change_mode(next_mode)
                step_duration += mode_change_delay

        if 0 == (self.bot.frame_counter % goal_update_rate):
            self.update_goal()

        ts = time.time()
        if self.step_timestamp:
            delay_needed = step_duration - (ts - self.step_timestamp)
            if delay_needed > 0.001:
                time.sleep(delay_needed)
        self.step_timestamp = ts

        print("frame %06d, output %06d, mode=%r, scores=%r" % (
            self.bot.frame_counter, self.output_frame_count,
            self.bot.current_mode, self.mode_scores))

    def choose_mode(self, reevaluation_interval=20):
        scores = list(map(self.evaluate_vibration_mode, range(len(self.bot.vibration_modes))))
        self.mode_scores = scores

        best_mode = 0
        for mode, score in enumerate(scores):
            last_frame_counter = self.bot.vibration_modes[mode].last_frame_counter
            if (score > scores[best_mode] or
                not last_frame_counter or
                (self.bot.frame_counter - last_frame_counter >= reevaluation_interval)):
                best_mode = mode
        return best_mode

    def record_bot_travel(self, from_pos, to_pos, distance_threshold=0.1):
        if not from_pos or not to_pos:
            return
        distance_squared = math.pow(to_pos[0] - from_pos[0], 2) + math.pow(to_pos[1] - from_pos[1], 2)
        if distance_squared > math.pow(distance_threshold, 2):
            return

        s = max(*self.inspiration.size)
        draw = ImageDraw.Draw(self.progress)
        draw.line((s*from_pos[0], s*from_pos[1], s*to_pos[0], s*to_pos[1]), fill=255, width=2)

    def update_goal(self):
        sub = ImageMath.eval("convert(a-b, 'L')", dict(a=self.inspiration, b=self.progress))
        long_distance_blur = sub.filter(self.large_blur).filter(self.large_blur)
        self.goal = ImageMath.eval("convert(a+b, 'L')", dict(a=sub, b=long_distance_blur))

        self.debugview.paste(im=0, box=(0, 0,)+self.debugview.size)
        s = max(*self.debugview.size)
        draw = ImageDraw.Draw(self.debugview)

        # Debug text
        velocities = ["v[%d] = %r" % (i, self.bot.vibration_modes[i].velocity)
                      for i  in range(len(self.bot.vibration_modes))]
        debug_text = "mode %d, frame %06d\nscores=%r\n%s" % (
            self.bot.current_mode, self.bot.frame_counter, self.mode_scores, '\n'.join(velocities))
        draw.text((1,1), debug_text, font=self.font, fill=255)

        # Show (magnified) velocity estimates for each vibration mode
        for mode in self.bot.vibration_modes:
            from_pos = self.bot.position
            zoom = 10
            if from_pos and mode.velocity:
                to_pos = (from_pos[0] + mode.velocity[0]*zoom, from_pos[1] + mode.velocity[1]*zoom)
                w = 1 + (mode == self.bot.vibration_modes[self.bot.current_mode])
                draw.line((s*from_pos[0], s*from_pos[1], s*to_pos[0], s*to_pos[1]), fill=255, width=w)

        status_im = Image.merge('RGB', (self.debugview, self.goal, self.progress))
        status_im.save('out/%06d.png' % self.output_frame_count)
        self.output_frame_count += 1

    def _sample_goal_int(self, pos, border):
        if pos[0] < 0 or pos[0] > self.goal.size[0]-1 or pos[1] < 0 or pos[1] > self.goal.size[1]-1:
            return border
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

    def evaluate_ray(self, vec, weight_step=0.75, weight_min=0.01, error_score=1e3):
        """Score a ray starting at the current location, with the given per-frame velocity"""

        pos = self.bot.position
        total = 0
        weight = 1.0
        step_length = 2.0 / min(*self.goal.size)

        if not vec:
            return error_score
        vec_len = math.sqrt(math.pow(vec[0], 2) + math.pow(vec[1], 2))
        if vec_len <= 0:
            return error_score
        step_vec = (vec[0] * step_length / vec_len, vec[1] * step_length / vec_len)

        while weight > weight_min:
            pos = (pos[0] + step_vec[0], pos[1] + step_vec[1])
            total += self.sample_goal(pos) * weight
            weight = weight * weight_step

        return total

    def evaluate_ray_bundle(self, vec):
        if not vec:
            return self.evaluate_ray(vec)
        total = 0
        for i in range(-2,3):
            angle = i * (math.pi / 180.0 * 5.0)
            s = math.sin(angle)
            c = math.cos(angle)
            rotated = (vec[0]*c - vec[1]*s, vec[0]*s + vec[1]*c)
            total += self.evaluate_ray(rotated)
        return total

    def evaluate_vibration_mode(self, index):
        mode = self.bot.vibration_modes[index]
        return self.evaluate_ray_bundle(mode.velocity)


def main():
    a = GreatArtist(WiggleBot(), sys.argv[1])
    try:
        while True:
            a.step()
    finally:
        a.bot.motors.off()

if __name__ == "__main__":
    main()

