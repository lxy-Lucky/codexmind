/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        'bg-deep':     '#06090f',
        'bg-base':     '#0a0f1a',
        'bg-surface':  '#0f1628',
        'bg-elevated': '#141d33',
        'bg-hover':    '#1a2540',
        'border-dim':  '#1c2a4a',
        'border-bright':'#263758',
        'cyan':        '#00d4ff',
        'cyan-dim':    'rgba(0,212,255,0.12)',
        'amber':       '#ffb347',
        'red-accent':  '#ff4757',
        'green-accent':'#26de81',
        'purple':      '#a55eea',
        'text-primary':'#e4eaf6',
        'text-secondary':'#8899bb',
        'text-muted':  '#556a8e',
        'text-code':   '#c8d6f0',
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', 'monospace'],
        display: ['"DM Serif Display"', 'serif'],
        body: ['"Source Serif 4"', 'serif'],
      },
      borderRadius: {
        sm: '6px',
        md: '10px',
        lg: '14px',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}


