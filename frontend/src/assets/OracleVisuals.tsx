import React from 'react';

export const OraclePillLogo: React.FC<{ size?: number }> = ({ size = 34 }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 64 64"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <rect width="64" height="64" rx="14" fill="#C74634" />
    <path
      d="M46 10L64 28V64H38L16 42L46 10Z"
      fill="#A33324"
      fillOpacity="0.45"
    />
    <rect
      x="13"
      y="21"
      width="38"
      height="22"
      rx="11"
      stroke="#FFFFFF"
      strokeWidth="5.5"
    />
  </svg>
);

export const OracleWaveCircuitBackdrop: React.FC = () => (
  <svg
    className="nm-hero-art"
    viewBox="0 0 900 260"
    preserveAspectRatio="xMidYMid slice"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <defs>
      <linearGradient id="nmWaveGrad1" x1="0" y1="130" x2="900" y2="60" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stopColor="#C74634" stopOpacity="0.85" />
        <stop offset="50%" stopColor="#FF6B4A" stopOpacity="0.65" />
        <stop offset="100%" stopColor="#FFA45B" stopOpacity="0.15" />
      </linearGradient>
      <linearGradient id="nmWaveGrad2" x1="80" y1="220" x2="860" y2="40" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stopColor="#8F2519" stopOpacity="0.6" />
        <stop offset="55%" stopColor="#F25C3B" stopOpacity="0.5" />
        <stop offset="100%" stopColor="#1D4E6E" stopOpacity="0.3" />
      </linearGradient>
    </defs>

    {/* Capas diagonales inspiradas en Imagen 3 */}
    <path d="M0 260L320 260L0 140Z" fill="#C74634" fillOpacity="0.22" />
    <path d="M0 140L320 260L470 260L0 85Z" fill="#2A2F3A" fillOpacity="0.28" />

    {/* Ondas de energía cálida inspiradas en Imagen 2 */}
    <path
      d="M-40 165 C 120 55, 240 235, 410 115 C 560 15, 690 210, 940 85"
      stroke="url(#nmWaveGrad1)"
      strokeWidth="3.5"
    />
    <path
      d="M-20 195 C 150 90, 270 250, 440 135 C 590 35, 720 225, 930 115"
      stroke="url(#nmWaveGrad2)"
      strokeWidth="12"
      strokeOpacity="0.35"
    />
    <path
      d="M20 135 C 180 35, 290 205, 465 95 C 610 10, 745 180, 920 60"
      stroke="#FF8A65"
      strokeWidth="1.5"
      strokeDasharray="6 6"
      strokeOpacity="0.55"
    />

    {/* Trazas de circuito e indicadores nodales inspirados en Imagen 5 */}
    <g stroke="#FF784F" strokeOpacity="0.4" strokeWidth="1.4">
      <polyline points="620,10 665,65 710,65 745,110" />
      <circle cx="745" cy="110" r="3" fill="#FF9E6D" />
      <polyline points="680,0 725,50 770,50 815,105" />
      <circle cx="815" cy="105" r="3" fill="#FFFFFF" />
      <polyline points="755,0 795,45 845,85" />
      <circle cx="845" cy="85" r="2.5" fill="#FF6B4A" />
    </g>

    {/* Textura binaria sutil inspirada en Imagen 5 */}
    <text x="690" y="185" fill="#FF6B4A" fillOpacity="0.16" fontFamily="JetBrains Mono" fontSize="11">
      01001110 01001101 01001111 01000011 01001001
    </text>
    <text x="720" y="205" fill="#FF6B4A" fillOpacity="0.13" fontFamily="JetBrains Mono" fontSize="11">
      10110010 01101001 11010100 00101111 10010110
    </text>
  </svg>
);
