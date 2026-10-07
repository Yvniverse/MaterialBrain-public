<script>
const paths = {
  grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  brain:
    'M9 3a3 3 0 0 0-3 3v1a4 4 0 0 0-2 7 4 4 0 0 0 5 6h1V4z M15 3a3 3 0 0 1 3 3v1a4 4 0 0 1 2 7 4 4 0 0 1-5 6h-1V4z M6 10h4 M14 14h4',
  cube: 'm12 3 9 5v8l-9 5-9-5V8z M3 8l9 5 9-5 M12 13v8 M7 5.8l9 5',
  drawer: 'M4 3h16v18H4z M4 9h16 M4 15h16 M10 6h4 M10 12h4 M10 18h4',
  search: 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  chevron: 'm9 5 7 7-7 7',
  plus: 'M12 5v14 M5 12h14',
  check: 'm5 12 4 4L19 6',
  close: 'm6 6 12 12 M6 18 18 6',
  doc: 'M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h6',
  clock: 'M12 8v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  folder: 'M3 6h7l2 2h9v12H3z M3 6V4h6l2 2',
  route: 'M4 5h3a3 3 0 0 1 0 6h10a3 3 0 0 1 0 6h-2 M4 2v6 M20 17l-4 4-4-4',
  pin: 'M19 9c0 5-7 12-7 12S5 14 5 9a7 7 0 0 1 14 0z M14 9a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
  refresh: 'M20 7v5h-5 M4 17v-5h5 M5 8a8 8 0 0 1 13-3l2 3 M4 16l2 3a8 8 0 0 0 13-3',
  expand: 'M8 3H3v5 M16 3h5v5 M3 16v5h5 M21 16v5h-5',
  send: 'm21 3-7 18-4-7-7-4z M10 14 21 3',
  settings:
    'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  chart: 'M4 20V10 M10 20V4 M16 20v-7 M22 20H2',
  layers: 'm12 3 10 5-10 5L2 8z M2 12l10 5 10-5 M2 16l10 5 10-5',
  activity: 'M2 12h5l3-8 4 16 3-8h5',
  bell: 'M5 17h14l-2-3V8a5 5 0 0 0-10 0v6z M10 21h4',
  download: 'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5',
  user: 'M16 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M4 22v-3a8 8 0 0 1 16 0v3',
  more: 'M4 12h.01 M12 12h.01 M20 12h.01',
  history: 'M3 11V4 M3 11h7 M4 7a9 9 0 1 1-1 9 M12 7v6l4 2',
  link: 'm10 14 4-4 M8 16l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0 M13 17a4 4 0 0 0 6 0l4-4a4 4 0 0 0-6-6l-1 1',
  chip: 'M7 7h10v10H7z M3 8h4 M3 12h4 M3 16h4 M17 8h4 M17 12h4 M17 16h4 M8 3v4 M12 3v4 M16 3v4 M8 17v4 M12 17v4 M16 17v4',
  wallet: 'M3 6h18v14H3z M3 6V3h15 M16 11h5v5h-5z',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12 M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  menu: 'M4 6h16 M4 12h16 M4 18h16',
  compare: 'M8 3v18 M16 3v18 M3 8l5-5 5 5 M11 16l5 5 5-5',
  info: 'M12 11v6 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  cart: 'M2 3h3l3 13h11l3-9H6 M9 21h.01 M18 21h.01',
}
export default {
  name: 'GIcon',
  props: { name: { type: String, default: 'cube' }, size: { type: [Number, String], default: 20 } },
  computed: {
    path() {
      return paths[this.name] || paths.cube
    },
  },
}
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="path" />
  </svg>
</template>
