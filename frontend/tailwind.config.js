/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: { extend: { fontFamily: { sans: ['DM Sans', 'sans-serif'], display: ['Manrope', 'sans-serif'] }, colors: { ink: '#17233c', muted: '#7c879b', canvas: '#f7f8fc', brand: '#6258e8', mint: '#21b88a' } } },
  plugins: [],
}
