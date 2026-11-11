# Game checklist (what the judge looks at; the judge reads screenshots, frame strips and numbers, never code)

- **States:** title, playing, won or lost, and back to title, each clearly different on screen. Pause is visible.
- **Feedback:** every input answers at once, in layers: motion, sound, and a small visual mark. Hits feel weighty.
- **Controls:** holding a direction moves at once and keeps moving at a steady speed until release (keydown and keyup), never waiting for the OS key repeat. Movement feels quick, not floaty.
- **Loop:** run ALL game logic and drawing from requestAnimationFrame (a fixed-step accumulator is fine). No setInterval or setTimeout game loop: the check freezes requestAnimationFrame, and a game that keeps running then can never pass.
- **Motion:** every movement eased; nothing snaps linearly. Particles are few and short.
- **Reduced motion:** with `prefers-reduced-motion`, nothing moves for effect; feedback fades in place.
- **Look:** current, not dated. A clear identity: material or pattern, purposeful colour, a named shape language.
- **Texture:** crisp and readable at play size, and better than flat colour, never muddier.
- **HUD:** small, quiet, tabular numbers, sentence case. No clutter.
- **Type:** a tuned system font stack; never default Times or Arial at default size.
- **Frames:** steady; FPS average and 1% low are in the measurements.
- **Phone width:** at 375 px nothing runs off screen or overlaps.
- **Dated tells** (each one is a fix; two or more cap the score below 8): glossy or bevelled buttons, heavy drop shadows, lens flare or bloom on everything, gradient-heavy panels, untuned default fonts, clip-art icons, a cluttered HUD, particles with no restraint, motion with no easing.
