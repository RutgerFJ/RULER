import daisyui from "./core/static/css/daisyui.mjs";

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./core/templates/**/*.html",
    "./core/static/js/**/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [daisyui],
  daisyui: {
    themes: ["light", "dark"],
  },
}
