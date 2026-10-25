"""Original SVG property illustrations, matching the board's storybook palette.

Regenerate with: python3 scripts/property_art.py
The PDFs specify places/tales but have no embedded illustrations. Names, prices
and rent stay in board.json and are rendered as accessible live HTML, not artwork.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'static/art/properties'


def tree(x, y, s=1, color='#477c60', pine=False):
    leaves = (f'<path d="M0-100-29-45h13L-36-17h72L16-45h13Z" fill="{color}"/><path d="M0-100v83h36L16-45h13Z" fill="#244f47" opacity=".35"/>' if pine else f'<path d="M-36-58-22-90 8-98 36-77 38-46 11-28-21-32Z" fill="{color}"/><path d="m8-98 3 70 27-18-2-31Z" fill="#163f3a" opacity=".27"/>')
    return f'<g transform="translate({x} {y}) scale({s})"><path d="M-5 0-4-60h9L6 0Z" fill="#8b6143"/>{leaves}</g>'


def house(x=160, y=171, s=1, wall='#e6d7ad', roof='#92617c'):
    return f'''<g transform="translate({x} {y}) scale({s})">
    <path d="M-48 0v-67h78V0Z" fill="{wall}"/><path d="M30-67 53-79V-9L30 0Z" fill="#baac8e"/>
    <path d="m-58-65 49-48 49 48Z" fill="{roof}"/><path d="M-9-113 15-126 63-79 40-65Z" fill="#704a62"/>
    <path d="M-46-65h74" stroke="#7d644f" stroke-width="3"/>
    <path d="M-16 0v-31a10 10 0 0 1 20 0V0Z" fill="#765440"/><circle cx="-1" cy="-16" r="1.5" fill="#e7be64"/>
    <g fill="#f4d88e" stroke="#aa9870" stroke-width="2"><path d="M-36-52h14v17h-14Z"/><path d="M7-52h14v17H7Z"/></g>
    <path d="M-29-52v17M-36-44h14M14-52v17M7-44h14" stroke="#b29166"/>
    </g>'''


def tower(x, y, s=1, roof='#92617c', wall='#e1d2b2'):
    return f'''<g transform="translate({x} {y}) scale({s})"><path d="M-16 0v-100h32V0Z" fill="{wall}"/><path d="M7-100h9V0H7Z" fill="#baac8e"/><path d="M-26-96 0-147 26-96Z" fill="{roof}"/><path d="M0-147 9-96h17Z" fill="#704a62"/><path d="M-5-77a5 5 0 0 1 10 0v15H-5Z" fill="#f6d58c"/><path d="M0-146v-14l19 5-19 6" fill="#ab5673" stroke="#8b6143"/></g>'''


def castle(dark=False, rose=False):
    wall = '#67617c' if dark else '#e3d4ae'
    roof = '#4d3c68' if dark else '#a5617e'
    return tower(104,174,.88,roof,wall)+tower(220,173,.9,roof,wall)+house(163,176,1.12,wall,roof)+tower(167,127,.7,roof,wall)+(flowers(85,177)+flowers(232,178) if rose else '')


def flowers(x, y, s=1):
    return f'<g transform="translate({x} {y}) scale({s})"><path d="m-13 0 4-15M3 0l4-22M17 0l-2-12" stroke="#46734f" stroke-width="2"/><g fill="#b77191"><circle cx="-9" cy="-17" r="5"/><circle cx="7" cy="-25" r="6"/><circle cx="15" cy="-14" r="5"/></g><g fill="#f1cc73"><circle cx="-9" cy="-17" r="2"/><circle cx="7" cy="-25" r="2"/><circle cx="15" cy="-14" r="2"/></g></g>'


def wheel(x, y, r=18):
    return f'<g transform="translate({x} {y})" fill="none" stroke="#bc9650" stroke-width="5"><circle r="{r}"/><path d="M-{r} 0h{r*2}M0-{r}v{r*2}" stroke-width="2"/><circle r="4" fill="#e2bb66"/></g>'


def book(x, y, s=1):
    return f'<g transform="translate({x} {y}) scale({s})"><path d="M0 0q-25-18-54-10v-41q28-7 54 10 26-17 54-10v41Q25-18 0 0Z" fill="#f9eac8" stroke="#9b754b" stroke-width="3"/><path d="M0-41V0M-44-40l31 6M-44-29l31 6M13-34l31-6M13-23l31-6" stroke="#ba9866" stroke-width="2"/></g>'


ART = {}
ART[1] = ''.join(tree(x,y,s,'#335a4b',True) for x,y,s in [(62,173,1.15),(111,169,1.45),(166,175,1.1),(215,172,1.4),(267,173,.9)])+'<path d="m158 180 9-33 9-24" fill="none" stroke="#ccbd8c" stroke-width="10"/>'
ART[3] = house(160,174,1.15,'#b87d51','#9b5b55')+'<path d="m93 99 55-50 56 50" fill="none" stroke="#fff0d2" stroke-width="6" stroke-dasharray="8 7"/><g fill="#d17c83"><circle cx="112" cy="124" r="5"/><circle cx="192" cy="128" r="5"/></g>'+flowers(78,175)+flowers(246,175)
ART[5] = house(157,174,1.12,'#cbad76','#8e5e65')+'<path d="M112 144h96v10h-96ZM122 154v21M197 154v21" fill="#8b6143" stroke="#70543c" stroke-width="3"/><g fill="#d2aa64" stroke="#795b3b" stroke-width="2"><circle cx="145" cy="131" r="8"/><path d="M141 139v12h9v-12M141 144h-9M150 144h10"/><path d="m174 138 13-16 11 9-15 15Z"/></g>'
ART[6] = tree(62,175,.8)+house(164,174,1.25,'#d9d0c4','#76617f')+tower(213,170,.68,'#76617f')+'<path d="M95 177h137" stroke="#b69a77" stroke-width="5"/>'
ART[8] = '<g stroke="#b38d4d" stroke-width="3"><path d="M105 146q-22-58 15-73 9-23 39-19 40-8 45 24 33 25 6 70Z" fill="#dc9a54"/><ellipse cx="157" cy="109" rx="30" ry="53" fill="#e7b16a"/><path d="M140 142V98q17-25 34 0v44Z" fill="#79566e"/><path d="M158 53q-10-16 6-22" fill="none" stroke="#71905a" stroke-width="7"/><path d="M111 148h98v10h-98Z" fill="#d5b66d"/></g>'+wheel(120,164,19)+wheel(198,164,19)+'<path d="M143 105h28" stroke="#dcc890" stroke-width="3"/>'
ART[9] = castle()+ '<path d="M153 173v-36q12-18 24 0v36" fill="#e7bd6d"/><path d="M156 141h18M165 133v40" stroke="#96724e" stroke-width="2"/>'
ART[11] = castle(True)+'<path d="m77 177 16-30-15-12 28-26M229 178l13-28-17-13 21-29" fill="none" stroke="#354d40" stroke-width="8"/><path d="M153 140h22v26h-22Z" fill="#9cae72"/>'
ART[12] = '<path d="M113 168h96v9h-96ZM151 147h19v21h-19Z" fill="#a17b49"/><path d="M105 109q47-17 91 0-4 37-43 38-38 0-48-38Z" fill="#dfb969" stroke="#ae8746" stroke-width="3"/><path d="m194 112 45-25-13 30-35 9M110 112H91q-17-28 4-30h14" fill="none" stroke="#d7af60" stroke-width="8"/><path d="M137 101h36l-8-11h-20Z" fill="#ebcd85"/><path d="M157 87q-28-22 1-36 28-16 0-37" fill="none" stroke="#8d91b0" stroke-width="11" opacity=".7"/>'
ART[13] = tower(153,176,1.04)+wheel(218,152,26)+'<path d="m214 175 5-47M198 176h45" stroke="#956b46" stroke-width="5"/>'
ART[14] = castle(rose=True)
ART[15] = '<path d="M91 168h143v10H91Z" fill="#bba986"/><path d="M118 145q18-16 19-48l17-20 12 10 7 37 26 14q23 5 22 15h-95Z" fill="#b8dbe1" stroke="#769eaf" stroke-width="3"/><path d="m138 99 16 9 14 32M126 153h94M153 82l2 21" fill="none" stroke="#edf7ee" stroke-width="5"/><path d="M115 149v21h10l3-20" fill="#96c0cf"/><path d="m196 70 3 10 10 3-10 3-3 10-3-10-10-3 10-3Z" fill="#eee1b6"/>'
ART[16] = house(162,174,1.06,'#dccb9a','#baa459')+'<path d="m98 105 54-58 59 58" fill="none" stroke="#d6bc70" stroke-width="13"/><path d="m109 101 45-48M121 103l35-47M138 104l20-43M169 65l18 37M180 73l18 30" stroke="#8e824f" stroke-width="3"/>'+flowers(76,175)
ART[18] = house(160,174,1.1,'#aa8963','#79604f')+'<path d="M108 115h81M108 128h81M108 140h81M108 153h28M167 153h22M108 166h28M167 166h22" stroke="#6f5844" stroke-width="4"/><path d="m101 102 47-47M113 101l37-38M178 72l31 29" stroke="#b69c70" stroke-width="5"/>'+tree(252,175,.6)
ART[19] = house(160,174,1.08,'#c48a70','#76545b')+'<path d="M185 68V40h17v46" fill="#a76d57"/><g fill="none" stroke="#e5baa0" stroke-width="2"><path d="M108 111h82M108 125h82M108 139h29M166 139h24M108 153h29M166 153h24M108 167h29M166 167h24M124 111v14M156 111v14M179 111v14M114 125v14M130 139v14M180 139v14M117 153v14M172 153v14"/></g>'+flowers(85,175)
ART[21] = tree(65,175,1.1, pine=True)+tree(240,175,1.25,pine=True)+'<path d="M138 195q61-28 25-53-21-21 4-40" fill="none" stroke="#ccbc91" stroke-width="22"/><path d="M145 157q-9-43 12-49 22 5 14 49Z" fill="#b9545e"/><path d="M149 157h20M155 113v37" stroke="#874150" stroke-width="3"/><path d="M173 152h14v13h-14Z" fill="#bf9259"/>'
ART[23] = ''.join(flowers(x,y,s) for x,y,s in [(72,176,1),(121,155,.8),(162,178,1.3),(209,155,.8),(253,175,1)])+'<path d="M46 185q100-19 228 0" stroke="#729063" stroke-width="9" fill="none"/>'
ART[24] = house(161,174,1.03,'#ead9b4','#b26e72')+'<path d="M111 147h90M118 145v30M194 145v30" stroke="#c3b187" stroke-width="5"/><path d="M75 158v21M89 158v21M231 158v21M245 158v21M69 165h28M224 165h28" stroke="#eee1ba" stroke-width="5"/>'+flowers(80,176)+flowers(240,176)
ART[25] = '<path d="m77 124 112-46 60 47-116 45Z" fill="#8c6585" stroke="#c9a75d" stroke-width="6"/><path d="m103 125 81-31 38 31-87 31Z" fill="#b18495" stroke="#dfc681" stroke-width="3"/><path d="m137 124 27-12 19 12-27 13Z" fill="#e4bd67"/><path d="m75 122-14 3M82 133l-15 6M93 143l-13 9M109 154l-12 11M235 118l15-2M244 128l14-1" stroke="#d6b66c" stroke-width="3"/><ellipse cx="162" cy="178" rx="66" ry="8" fill="#254e48" opacity=".15"/>'
ART[26] = house(92,174,.73,'#d5c29d','#956176')+house(222,173,.8,'#e2cfac','#ab7276')+house(158,178,1.05,'#e6d7b2','#846578')+flowers(270,178,.9)
ART[27] = house(165,173,1.28,'#c3b69c','#71566b')+'<g fill="#efcc80" stroke="#8c795a" stroke-width="3"><path d="M123 101h19v34h-19ZM166 101h19v34h-19Z"/></g>'+book(159,181,.65)
ART[28] = '<ellipse cx="160" cy="162" rx="49" ry="22" fill="#a9b1a2" stroke="#748a80" stroke-width="4"/><path d="M111 145v22q48 27 98 0v-22q-48 25-98 0Z" fill="#c0beaa" stroke="#8b9b8a" stroke-width="3"/><ellipse cx="160" cy="145" rx="48" ry="17" fill="#6a9695"/><path d="M126 144V84M195 144V84" stroke="#896a48" stroke-width="8"/><path d="m106 88 51-49 59 49Z" fill="#8f6477"/><path d="M137 91h45M160 92v40" stroke="#b28b57" stroke-width="4"/><path d="M150 129h20l-3 16h-14Z" fill="#c7a96b"/>'
ART[29] = castle(True)+'<path d="M150 142q14-18 28 0v27h-28Z" fill="#d5c493"/><path d="M164 166v-19" stroke="#52744f" stroke-width="3"/><path d="M159 145q5-9 10 0-5 8-10 0" fill="#b85970"/>'
ART[31] = tree(153,180,1.52,'#55836a')+'<path d="M128 177v-37q24-35 48 0v37Z" fill="#65766a"/><path d="M139 177v-30q13-22 26 0v30Z" fill="#d1bb7b"/><path d="m126 130 26-22 26 22Z" fill="#ad8290"/>'+flowers(76,176)+flowers(241,177)
ART[32] = '<path d="M28 143q62-13 125 0 80-15 145 1v45H28Z" fill="#78aaa6"/><path d="M57 153q40-13 79 0M175 166q49-14 92-1" stroke="#b9d3c5" stroke-width="4" fill="none"/><path d="m99 170 18-35 30-6 27 42Z" fill="#aab8a9"/><path d="M183 146q-32-48 3-61 39 3 25 60Z" fill="#b798aa" stroke="#907c99" stroke-width="3"/><path d="m187 94 6 47M177 102l13 38M203 103l-6 38" stroke="#debfcb" stroke-width="3"/><circle cx="194" cy="140" r="12" fill="#eddfbf"/>'
ART[34] = '<path d="M42 164q90-18 235 0v25H42Z" fill="#76a19b"/><path d="m78 136 24 34h114l26-34Z" fill="#906746" stroke="#694e3a" stroke-width="4"/><path d="M161 137V32M113 137V55" stroke="#7c5c40" stroke-width="6"/><path d="m169 49 53 62h-53ZM151 59l-36 52h36ZM106 72l-26 43h26Z" fill="#eddfbd"/><path d="M163 33h39l-9-10h-30Z" fill="#874f69"/><path d="M117 149h83" stroke="#c59a5c" stroke-width="4"/><g fill="#d9b86b"><circle cx="125" cy="158" r="4"/><circle cx="158" cy="158" r="4"/><circle cx="191" cy="158" r="4"/></g>'
ART[35] = tree(89,176,.95,'#55815a')+tree(221,173,1.15,'#496f56')+'<g fill="#bb616a"><circle cx="70" cy="110" r="6"/><circle cx="101" cy="119" r="6"/><circle cx="218" cy="95" r="6"/><circle cx="240" cy="116" r="6"/></g><path d="M139 149q-17-22-31-5-10 24 13 37 22 3 33-19 6-22-15-13Z" fill="#b65d65"/><path d="m135 147 5-13" stroke="#826145" stroke-width="4"/><path d="M140 141q10-14 22-5-7 12-22 5" fill="#70935d"/>'
ART[37] = '<path d="m65 176 15-74 34-41 37-15 62 19 42 58 12 53Z" fill="#929a8c"/><path d="m151 46-22 130h138l-12-53-42-58Z" fill="#77877d"/><path d="M112 176v-63q42-49 83 0v63Z" fill="#4a5656"/><path d="M108 176V109h92v67M103 108h103" fill="none" stroke="#ad8a58" stroke-width="10"/><path d="m135 180 6-33h35l8 33" fill="#8c7970"/><path d="m142 147 8-21 12 19 11-27 10 29" fill="#9ec2bb"/>'+wheel(143,178,9)+wheel(180,178,9)
ART[39] = castle()+ '<path d="M132 166v-42q27-40 54 0v42Z" fill="#c6aa66" stroke="#926f40" stroke-width="4"/><path d="M141 158v-31q18-26 36 0v31Z" fill="#a4c1c2"/><path d="m149 149 19-28M154 156l17-25" stroke="#e0e7d7" stroke-width="4"/>'


def main():
    board=json.loads((ROOT/'server/board.json').read_text())
    ownable={sq['id'] for sq in board['squares'] if sq['type'] in ('property','railroad','utility')}
    assert set(ART)==ownable, 'Every ownable space needs its own illustration'
    OUT.mkdir(parents=True,exist_ok=True)
    for square in board['squares']:
        if square['id'] not in ART:
            continue
        night=square['id'] in (1,11,29,37,39)
        sky='#d7dfda' if night else '#e8eee0'
        art=f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200" fill="none">
        <rect width="320" height="200" fill="{sky}"/><circle cx="252" cy="42" r="22" fill="#f1deb0" opacity=".8"/>
        <path d="M0 143Q72 91 164 129 258 89 320 130v70H0Z" fill="#91ac8d"/>
        <path d="M0 163q98-40 185-9 77-14 135 4v42H0Z" fill="#72977b"/>
        <ellipse cx="161" cy="176" rx="105" ry="12" fill="#244f47" opacity=".14"/>
        {ART[square['id']]}
        </svg>'''
        (OUT/f"{square['id']}.svg").write_text(art)
    print(f'Created {len(ART)} original property illustrations')


if __name__=='__main__':
    main()
