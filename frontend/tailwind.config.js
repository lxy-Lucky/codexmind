/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        'bg-deep':     '#1e2028',
        'bg-base':     '#272a34',
        'bg-surface':  '#2f3340',
        'bg-elevated': '#373c4c',
        'bg-hover':    '#404758',
        'border-dim':  '#3d4455',
        'border-bright':'#556178',
        'cyan':        '#5ec4d4',
        'cyan-dim':    'rgba(94,196,212,0.12)',
        'amber':       '#f0b45a',
        'red-accent':  '#e85d6a',
        'green-accent':'#56c88a',
        'purple':      '#b07ee8',
        'text-primary':'#dde2f0',
        'text-secondary':'#9aa3b8',
        'text-muted':  '#6b7690',
        'text-code':   '#c8cfe0',
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


