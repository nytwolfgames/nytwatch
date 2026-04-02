# Design System Specification: Clinical Precision

## 1. Overview & Creative North Star: "The Digital Surgeon"
This design system moves away from the heavy, protective aesthetics of dark mode into a realm of **Clinical Precision**. The Creative North Star is "The Digital Surgeon"—an environment that feels sterile, hyper-efficient, and authoritative.

We break the "generic SaaS" template by rejecting standard grid-and-border layouts. Instead, we utilize **Intentional Asymmetry** and **Tonal Layering**. By overlapping `surface-container` elements and utilizing high-contrast typography scales, we create an editorial experience that feels custom-built for high-stakes code auditing. The UI does not just hold data; it organizes it through intellectual hierarchy.

## 2. Colors & Surface Architecture
The palette transitions from sterile whites to deep, intellectual blues. We avoid flat UI by using a sophisticated "nested" approach to surfaces.

### The "No-Line" Rule
Traditional 1px solid borders are strictly prohibited for sectioning. Boundaries must be defined solely through background color shifts. For instance, a side panel using `surface-container-low` should sit directly against a `surface` background. The eye should perceive the change in depth through color, not a "stroke."

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers—like stacked sheets of fine technical paper.
- **Base Layer:** `surface` (#f7f9fb) – The canvas.
- **Mid Layer:** `surface-container-low` (#f2f4f6) – Secondary navigation or utility bars.
- **Top Layer:** `surface-container-lowest` (#ffffff) – Primary content cards and code editors.
- **Nesting:** Always place a `surface-container-lowest` element inside a `surface-container` or `surface-variant` area to create a "lifted" focal point without using shadows.

### The "Glass & Gradient" Rule
To inject "soul" into the technical layout:
- **Primary CTAs:** Use a subtle linear gradient from `primary` (#004ac6) to `primary-container` (#2563eb) at a 135-degree angle. This adds a microscopic sense of volume.
- **Floating Modals:** Use Glassmorphism. Apply `surface-container-lowest` at 80% opacity with a `20px` backdrop-blur. This ensures the tool feels integrated into the environment rather than a detached pop-up.

## 3. Typography: Technical Editorial
We pair the geometric, "engineered" feel of **Space Grotesk** with the neutral, highly legible **Inter**.

* **Display & Headlines (Space Grotesk):** Use for high-level data summaries and page titles. The wide apertures and technical terminals of Space Grotesk signal "Code" and "Precision."
* *System Note:* Use `display-lg` sparingly to highlight critical audit scores.
* **Body & Labels (Inter):** Used for all analytical text. Inter’s tall x-height ensures that even at `body-sm` (0.75rem), complex error logs remain readable.
* **Hierarchy:** Maintain a dramatic scale jump between `headline-lg` and `body-md` to create an editorial "entry point" for the user's eye.

## 4. Elevation & Depth: Tonal Layering
In this system, light is the architect. We move away from the "shadow-heavy" look of the past.

* **The Layering Principle:** Depth is achieved by stacking. A card (`surface-container-lowest`) placed on a section (`surface-container-high`) creates a natural, soft lift.
* **Ambient Shadows:** If an element must float (e.g., a dropdown), use an ultra-diffused shadow: `box-shadow: 0 10px 30px rgba(25, 28, 30, 0.04)`. The shadow color is a low-opacity version of `on-surface`, never pure black.
* **The "Ghost Border" Fallback:** If a container requires definition against an identical background color, use a "Ghost Border": `outline-variant` (#c3c6d7) at **15% opacity**.

## 5. Component Logic

### Buttons
- **Primary:** Gradient fill (`primary` to `primary-container`). `0.375rem` (md) corner radius. Typography: `label-md` (bold).
- **Secondary:** `surface-container-highest` background with `on-surface` text. No border.
- **Tertiary:** Transparent background. Text color is `primary`. On hover, apply a 5% `primary` tint.

### Code Blocks & Syntax
- **Container:** `surface-container-lowest` (#ffffff).
- **Syntax Highlighting:** Use a "Light Pro" theme. Keywords in `primary` (#004ac6), strings in `tertiary` (#943700), and comments in `outline` (#737686).
- **The Precision Edge:** Use a 2px vertical accent line of `surface-tint` on the left side of the active line in the code editor.

### Cards & Lists
- **The "No Divider" Rule:** Never use horizontal lines to separate list items. Use vertical white space (`spacing-4`) or alternating subtle shifts between `surface` and `surface-container-low`.
- **Nesting:** All cards must use `rounded-lg` (0.5rem).

### Input Fields
- **State:** Default state uses `surface-container-highest` as a subtle fill.
- **Focus:** Transition to a `ghost-border` (15% opacity `primary`) with a 2px inner-glow of `primary-fixed-dim`. This mimics a "lens" focusing on the data.

## 6. Do's and Don'ts

| Do | Don't |
| :--- | :--- |
| Use **Asymmetric white space** to lead the eye toward critical code vulnerabilities. | Don't use 1px #000 borders to define layout blocks. |
| Use **Tonal Layering** (Light Grey on White) to create hierarchy. | Don't use heavy drop shadows or "skeuomorphic" inner shadows. |
| Use **Space Grotesk** for any string that represents "Technical Data." | Don't use Space Grotesk for long-form body paragraphs. |
| Use **Glassmorphism** for overlays to maintain the "Clinical" transparency. | Don't use 100% opaque modals that "block" the context of the audit. |
| Follow the **0.25rem (4px) grid** strictly for all internal spacing. | Don't use "eye-balled" spacing or odd-numbered pixel values. |

## 7. Signature Interaction: The "Pulse"
When the Code Auditor Agent is "thinking" or "scanning," do not use a standard circular loader. Instead, use a linear, shimmering gradient (using `primary-fixed` and `primary-fixed-dim`) that moves across the top of the `surface-container-lowest` content area. This reinforces the "Clinical Precision" vibe—a silent, efficient scan.