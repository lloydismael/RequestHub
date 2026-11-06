"""Execute the profile avatar crop script to catch crop geometry regressions."""

import json
import shutil
import struct
import subprocess
import zlib
from io import BytesIO
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from .forms import AVATAR_MAX_EDGE, ProfileForm

PROFILE_TEMPLATE = Path("templates") / "accounts" / "profile_form.html"
CROP_START = "// Live avatar preview with an optional crop step before upload."
CROP_END = "// Settings changes are staged until explicitly saved."


def read_template():
    return (Path(settings.BASE_DIR) / PROFILE_TEMPLATE).read_text(encoding="utf-8")


@skipUnless(shutil.which("node"), "Node.js is required for JavaScript regressions")
class AvatarCropScriptTests(SimpleTestCase):
    """The crop stage must always be fully covered by the image.

    A width- or height-only fit leaves empty space inside the crop circle, so
    these tests pin the "cover" behaviour for wide, tall and square sources and
    assert that the zoom floor equals that cover scale.
    """

    def run_node(self, script):
        result = subprocess.run(
            [shutil.which("node"), "-"],
            input=script,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def crop_source(self):
            template = read_template()
            self.assertIn(CROP_START, template)
            block = template.split(CROP_START, 1)[1].split(CROP_END, 1)[0].rstrip()
            self.assertTrue(block.endswith("}());"), "unexpected crop script terminator")
            return block

    def test_cover_fit_and_drag_preservation(self):
        source = json.dumps(self.crop_source())
        script = """
const assert = require('node:assert/strict');
const fs = require('node:fs');

let source = SOURCE;

// Expose the internal helpers by evaluating the IIFE body with instrumented
// bindings: the crop branch runs only when the expected DOM nodes exist.
const listeners = {};
const classes = new Set();
const makeEl = (id) => ({
    id, hidden: true, style: {}, dataset: {}, attrs: {}, children: [],
    clientWidth: 0, offsetWidth: 0, naturalWidth: 0, naturalHeight: 0,
    classList: {
        add: (c) => classes.add(c),
        remove: (c) => classes.delete(c),
        contains: (c) => classes.has(c),
        toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
    },
    addEventListener(type, fn) { listeners[id + ':' + type] = fn; },
    removeEventListener() {},
    appendChild(child) { this.children.push(child); },
    replaceWith() {},
    setAttribute(k, v) { this.attrs[k] = v; },
    getContext() {
        return new Proxy({}, {get: () => () => ({})});
    },
    toDataURL: () => 'data:image/jpeg;base64,AAAA',
    getBoundingClientRect: () => ({left: 0, top: 0, width: 500, height: 500}),
    querySelector: () => null,
    querySelectorAll: () => [],
    dispatchEvent: () => true,
    closest: () => null,
    focus() {},
    click() {},
});

const stage = makeEl('avatar-crop-stage');
const image = makeEl('avatar-crop-image');
const overlay = makeEl('avatar-crop-overlay');
const zoom = makeEl('avatar-crop-zoom');
const root = makeEl('avatar-crop');
const photoInput = makeEl('id_profile_photo');
const preview = makeEl('profile-avatar-preview');

const nodes = {
    'avatar-crop': root,
    'avatar-crop-stage': stage,
    'avatar-crop-image': image,
    'avatar-crop-overlay': overlay,
    'avatar-crop-zoom': zoom,
    'avatar-crop-apply': makeEl('avatar-crop-apply'),
    'avatar-crop-cancel': makeEl('avatar-crop-cancel'),
    'avatar-crop-reset': makeEl('avatar-crop-reset'),
    'id_profile_photo': photoInput,
    'profile-avatar-preview': preview,
};

function sizeStage(px) {
    stage.clientWidth = px;
    stage.offsetWidth = px;
    stage.getBoundingClientRect = () => ({left: 0, top: 0, width: px, height: px});
}

const document = {
    hidden: false,
    getElementById: (id) => nodes[id] || null,
    querySelector: (sel) => (sel === '#id_profile_photo' ? photoInput : null),
    querySelectorAll: () => [],
    createElement: (tag) => makeEl('created-' + tag),
    addEventListener(type, fn) { listeners['document:' + type] = fn; },
        removeEventListener(type) { delete listeners['document:' + type]; },
    dispatchEvent: () => true,
    body: makeEl('body'),
    documentElement: makeEl('html'),
};

const window = {
    devicePixelRatio: 1,
    addEventListener() {},
    matchMedia: () => ({matches: false, addEventListener() {}}),
};
const ResizeObserver = function (cb) { this.observe = () => {}; this.disconnect = () => {}; };
const Event = function (type) { this.type = type; };
const CustomEvent = function (type) { this.type = type; };
const DataTransfer = function () { this.items = {add() {}}; this.files = []; };
const URL = {createObjectURL: () => 'blob:x', revokeObjectURL() {}};
const requestAnimationFrame = (fn) => { fn(); return 1; };
const cancelAnimationFrame = () => {};
window.requestAnimationFrame = requestAnimationFrame;
window.cancelAnimationFrame = cancelAnimationFrame;

// Evaluate the crop script. The extracted block starts partway into its
// enclosing IIFE, so wrap it in a function. The helpers are const-declared
// after the crop state, so stash a deferred accessor that is only invoked once
// the whole IIFE body has executed.
const INSTRUMENT =
    'globalThis.__crop = () => ({crop, state, computeBase, clampOffsets, syncResetState, openCrop, stopRepaintWatch});';
const ANCHOR = 'const state = { scale: 1, offsetX: 0, offsetY: 0, base: 1, ready: false };';
assert.ok(source.includes(ANCHOR), 'crop state declaration not found to instrument');
const body = source.slice(0, -'}());'.length);
const instrumented =
    '(function () {' +
    body.replace(ANCHOR, ANCHOR + INSTRUMENT) +
    '}());';

const fn = new Function(
    'document', 'window', 'ResizeObserver', 'Event', 'CustomEvent', 'DataTransfer',
    'URL', 'requestAnimationFrame', 'cancelAnimationFrame', 'navigator', 'Image',
    instrumented
);
fn(document, window, ResizeObserver, Event, CustomEvent, DataTransfer, URL,
   requestAnimationFrame, cancelAnimationFrame, {userAgent: 'node'}, function () {});

const api = globalThis.__crop();
assert.ok(api && api.state, 'crop internals were not captured');

// --- cover fit: the smaller axis fills the square stage -------------------
const cases = [
    {name: 'wide 1200x675', w: 1200, h: 675},
    {name: 'tall 600x1400', w: 600, h: 1400},
    {name: 'square 800x800', w: 800, h: 800},
];
for (const c of cases) {
    sizeStage(320);
    image.naturalWidth = c.w;
    image.naturalHeight = c.h;
    api.state.ready = false;
    api.syncResetState();
    const scale = api.state.scale;
    const expect = Math.max(320 / c.w, 320 / c.h);
    assert.ok(Math.abs(scale - expect) < 1e-9, `${c.name}: base ${scale} != cover ${expect}`);
    assert.ok(c.w * scale >= 320 - 1e-6, `${c.name}: rendered width does not cover the stage`);
    assert.ok(c.h * scale >= 320 - 1e-6, `${c.name}: rendered height does not cover the stage`);
    assert.ok(Math.abs(Number(zoom.min) - expect) < 1e-9, `${c.name}: zoom floor is not the cover scale`);
}

// --- a repaint must not discard the user's zoom or pan --------------------
sizeStage(320);
image.naturalWidth = 1200;
image.naturalHeight = 675;
api.state.ready = false;
api.syncResetState();
const baseScale = api.state.scale;

// simulate the user zooming in and dragging
api.state.scale = baseScale * 2;
api.state.offsetX = -40;
api.state.offsetY = -25;

// a repaint at the same stage size (ResizeObserver / deferred repaint)
api.syncResetState();
assert.ok(Math.abs(api.state.scale - baseScale * 2) < 1e-6,
    'repaint reset the user zoom back to the base fit');
assert.ok(Math.abs(api.state.offsetX - -40) < 1e-6, 'repaint reset offsetX');
assert.ok(Math.abs(api.state.offsetY - -25) < 1e-6, 'repaint reset offsetY');

// --- a genuine resize rescales proportionally instead of recentering ------
sizeStage(640);
api.syncResetState();
const grownBase = Math.max(640 / 1200, 640 / 675);
assert.ok(Math.abs(api.state.scale - grownBase * 2) < 1e-6,
    'a resize did not scale the user zoom proportionally');
assert.ok(api.state.offsetX <= 0 && api.state.offsetY <= 0,
    'the image no longer covers the stage after a resize');

// --- offsets are clamped so no empty space is ever visible ---------------
api.state.scale = baseScale * 6;
api.state.offsetX = 9999;
api.state.offsetY = 9999;
api.clampOffsets();
assert.ok(api.state.offsetX <= 0 && api.state.offsetY <= 0,
    'clampOffsets allowed the image to drift away from the top-left');
assert.ok(1200 * api.state.scale + api.state.offsetX >= 640 - 1e-6,
    'clampOffsets left a gap on the right edge');
assert.ok(675 * api.state.scale + api.state.offsetY >= 640 - 1e-6,
    'clampOffsets left a gap on the bottom edge');

// --- the panel-changed event drives the deferred repaint -----------------
// The listener is only registered when a crop opens against a stage that has
// no measurable size yet (the avatar panel is still hidden). Firing the event
// once the stage becomes measurable must complete the framing.
sizeStage(0);
image.naturalWidth = 1200;
image.naturalHeight = 675;
api.state.ready = true;
api.state.base = 0;
api.openCrop();
const onTabChanged = listeners['document:requesthub:profile-tab-changed'];
assert.ok(onTabChanged, 'the crop script does not listen for requesthub:profile-tab-changed');
assert.ok(api.state.base === 0, 'a zero-size stage should not establish a base fit');

sizeStage(320);
onTabChanged();
assert.ok(api.state.base > 0, 'the panel-changed event did not complete the deferred repaint');
assert.ok(Math.abs(api.state.scale - Math.max(320 / 1200, 320 / 675)) < 1e-9,
    'the deferred repaint did not apply the cover fit');
api.stopRepaintWatch();
assert.ok(!listeners['document:requesthub:profile-tab-changed'],
    'stopRepaintWatch did not remove the panel-changed listener');
console.log('avatar crop checks passed');
""".replace("SOURCE", source)
        self.run_node(script)


class ProfilePhotoNormalizationTests(SimpleTestCase):
    """The server must normalize an uploaded avatar on its own.

    The crop step is client-side only, so a bypassing or script-free client can
    still post an oversized or non-square image. `ProfileForm.clean_profile_photo`
    is the contract that keeps stored avatars square, RGB JPEGs of a bounded size.
    Constructing `ProfileForm` pulls in the form editor runtime and its database
    queries, so the field cleaner is exercised directly with its only input.
    """

    class _Form:
        """Minimal stand-in: the cleaner reads nothing but `cleaned_data`."""

        cleaned_data = {}

    def clean(self, upload):
        form = self._Form()
        form.cleaned_data = {"profile_photo": upload}
        try:
            return ProfileForm.clean_profile_photo(form), None
        except ValidationError as error:
            return None, error

    @staticmethod
    def upload_for(image, name="upload.png"):
        buffer = BytesIO()
        image.save(buffer, format=image.format or "PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def upload_with_dimensions(self, width, height):
        # Rewrite valid PNG metadata without allocating a large decoded image.
        data = bytearray(self.upload_for(Image.new("RGB", (1, 1))).read())
        data[16:24] = struct.pack(">II", width, height)
        data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
        return SimpleUploadedFile("large.png", bytes(data), content_type="image/png")

    def test_input_pixel_limit_is_checked_before_decoding(self):
        for width, height in ((4001, 4000), (16_000_001, 1)):
            with self.subTest(width=width, height=height):
                photo, error = self.clean(self.upload_with_dimensions(width, height))
                self.assertIsNone(photo)
                self.assertIsInstance(error, ValidationError)
                self.assertEqual(error.code, "image_too_large")

    def test_decompression_bomb_becomes_a_validation_error(self):
        try:
            photo, error = self.clean(self.upload_with_dimensions(20_000, 20_000))
        except Image.DecompressionBombError:
            self.fail("Oversized uploads must produce a form validation error.")
        self.assertIsNone(photo)
        self.assertIsInstance(error, ValidationError)
        self.assertEqual(error.code, "image_too_large")

    def test_wide_image_is_center_cropped_to_a_square(self):
        source = Image.new("RGB", (1000, 500))
        # Red on the left half, blue on the right, so the crop origin is provable.
        source.paste((254, 0, 0), (0, 0, 500, 500))
        source.paste((0, 0, 254), (500, 0, 1000, 500))
        photo, error = self.clean(self.upload_for(source, "wide.png"))
        self.assertIsNone(error)
        self.assertEqual(photo.name, "wide.jpg")
        with Image.open(photo) as result:
            # The square is bounded by the shorter axis and never up-scaled.
            self.assertEqual(result.size, (500, 500))
            self.assertEqual(result.format, "JPEG")
            # The kept band is the horizontal middle (x=250..750), so the result
            # starts red and ends blue: the crop is centred, not left-anchored.
            self.assertEqual(result.getpixel((0, 10)), (254, 0, 0))
            self.assertEqual(result.getpixel((499, 10)), (0, 0, 254))

    def test_small_image_is_not_upscaled(self):
        photo, error = self.clean(self.upload_for(Image.new("RGB", (40, 40)), "tiny.png"))
        self.assertIsNone(error)
        with Image.open(photo) as result:
            self.assertEqual(result.size, (40, 40))

    def test_tall_image_is_center_cropped_to_a_square(self):
        photo, error = self.clean(self.upload_for(Image.new("RGB", (600, 1400)), "tall.png"))
        self.assertIsNone(error)
        with Image.open(photo) as result:
            # The square comes from the shorter (width) axis, then the 600px edge
            # is bounded down to the maximum avatar edge.
            self.assertEqual(result.size, (AVATAR_MAX_EDGE, AVATAR_MAX_EDGE))

    def test_smaller_square_is_left_at_its_own_size(self):
        photo, error = self.clean(self.upload_for(Image.new("RGB", (480, 640)), "mid.png"))
        self.assertIsNone(error)
        with Image.open(photo) as result:
            self.assertEqual(result.size, (480, 480))

    def test_photo_extension_is_normalized_to_jpeg(self):
        for name in ("shot.png", "photo.jpeg", "weird.webp", "already.jpg"):
            with self.subTest(name=name):
                photo, error = self.clean(self.upload_for(Image.new("RGB", (80, 80)), name))
                self.assertIsNone(error)
                self.assertTrue(photo.name.endswith(".jpg"))
                self.assertFalse(photo.name.endswith(".jpeg"))

    def test_oversized_image_is_bounded_to_the_max_edge(self):
        photo, error = self.clean(self.upload_for(Image.new("RGB", (2000, 2000)), "big.png"))
        self.assertIsNone(error)
        with Image.open(photo) as result:
            self.assertEqual(result.size, (AVATAR_MAX_EDGE, AVATAR_MAX_EDGE))

    def test_non_image_upload_is_rejected(self):
        upload = SimpleUploadedFile("notes.txt", b"not an image", content_type="text/plain")
        photo, error = self.clean(upload)
        self.assertIsNone(photo)
        self.assertIsInstance(error, ValidationError)

    def test_missing_photo_is_ignored(self):
        photo, error = self.clean(None)
        self.assertIsNone(error)
        self.assertIsNone(photo)
