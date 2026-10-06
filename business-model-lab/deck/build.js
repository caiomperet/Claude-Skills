const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");
const { applyTheme } = require("/root/.claude/skills/synced/d95954b1-5014-4377-9843-cb91c354a053_c8832fb7-b9ff-4044-84ba-9bbf494c19c7/pptx/scripts/apply_theme.js");

const OUT = process.argv[2];

const THEME = {
  name: "Signal Steel",
  headFontFace: "Cambria",
  bodyFontFace: "Calibri",
  colors: {
    dk1: "1B1F24", lt1: "FFFFFF", dk2: "14213D", lt2: "EEF1F5",
    accent1: "F4A300", accent2: "2A9D8F", accent3: "5C6B7A", accent4: "C8553D",
    accent5: "8AA1B8", accent6: "3D5A80", hlink: "2A9D8F", folHlink: "3D5A80",
  },
};
const HEX = THEME.colors;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pres.title = "Beyond Tower Sharing";
pres.author = "Business Model Lab";
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
const C = pres.SchemeColor;

const W = 13.333;
const FOOT = "Beyond Tower Sharing  |  Business Model Lab";

// ---------------------------------------------------------------- layouts
pres.defineSlideMaster({
  title: "TITLE",
  background: { color: C.text2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.2, w: 11.5, h: 1.6, fontSize: 44, bold: true, color: C.background1, fontFace: THEME.headFontFace, valign: "bottom", align: "left" }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 3.95, w: 11.5, h: 1.2, fontSize: 20, color: C.accent1, valign: "top", align: "left" }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "SECTION",
  background: { color: C.text2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.8, y: 2.9, w: 11.5, h: 1.2, fontSize: 40, bold: true, color: C.background1, fontFace: THEME.headFontFace, valign: "middle", align: "left" }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.8, y: 4.1, w: 11.5, h: 0.9, fontSize: 18, color: C.accent5, valign: "top", align: "left" }, text: "" } },
  ],
});
pres.defineSlideMaster({
  title: "CONTENT",
  background: { color: C.background1 },
  margin: [0.5, 0.6, 0.6, 0.6],
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 12.1, h: 0.9, fontSize: 28, bold: true, color: C.text2, fontFace: THEME.headFontFace, valign: "middle", align: "left" }, text: "" } },
    { text: { text: FOOT, options: { x: 0.6, y: 7.05, w: 8, h: 0.3, fontSize: 10, color: C.accent3, margin: 0 } } },
  ],
  slideNumber: { x: 12.2, y: 7.05, w: 0.5, h: 0.3, fontSize: 10, color: C.accent3, align: "right" },
});

// ---------------------------------------------------------------- helpers
async function icon(name, hex) {
  const svg = ReactDOMServer.renderToStaticMarkup(React.createElement(fa[name], { color: "#" + hex, size: 256 }));
  const buf = await sharp(Buffer.from(svg)).resize(256, 256).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}
async function iconCircle(slide, name, x, y, d, circle, glyph, label) {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: circle }, line: { color: circle }, objectName: label + " circle" });
  const p = d * 0.25;
  slide.addImage({ data: await icon(name, glyph), x: x + p, y: y + p, w: d - 2 * p, h: d - 2 * p, objectName: label + " icon", altText: label });
}
const card = (slide, x, y, w, h, fill, name) =>
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.12, fill: { color: fill }, line: { color: fill }, objectName: name,
    shadow: { type: "outer", color: "9AA5B1", opacity: 0.25, blur: 6, offset: 2, angle: 90 } });
const txt = (slide, text, o) => slide.addText(text, { isTextBox: true, margin: 0, valign: "top", ...o });
const content = (title, sec) => { const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: sec }); s.addText(title, { placeholder: "title" }); return s; };
const source = (slide, t) => txt(slide, t, { x: 0.6, y: 6.68, w: 12.1, h: 0.3, fontSize: 10, color: C.accent3, italic: true });

(async () => {
  // ================================================================ 1. Title
  pres.addSection({ title: "Opening" });
  let s = pres.addSlide({ masterName: "TITLE", sectionTitle: "Opening" });
  s.addText("Beyond Tower Sharing", { placeholder: "title" });
  s.addText("Why tower growth is slowing, and the business ideas with the most potential to transform the company", { placeholder: "body" });
  await iconCircle(s, "FaBroadcastTower", 0.8, 0.9, 1.0, HEX.accent1, HEX.dk2, "Tower");
  txt(s, "Strategy study  |  Reference company: SBA Communications (~44k sites, ~US$ 2.85B revenue)", { x: 0.8, y: 6.5, w: 11.5, h: 0.4, fontSize: 14, color: C.accent5 });
  s.addNotes("This deck summarizes the study: first the diagnosis of the tower market, then the growth ambition we defined, then the business ideas with the most potential, ordered by priority.");

  // ================================================================ 2. Executive summary
  s = content("Executive summary", "Opening");
  const exec = [
    ["FaChartLine", "The engine is slowing", "Carrier consolidation, RAN sharing, the end of the 5G capex wave and tenant defaults have cut organic tower growth to low single digits."],
    ["FaBroadcastTower", "5G did not kill the tower", "Mid-band 5G runs on existing macro sites. But new demand is moving to energy, indoor, edge compute and non-telecom users."],
    ["FaLayerGroup", "The bar is high", "To become the main growth engine, a new business needs ~US$ 0.5B revenue by year 5 and tower-like margins over time."],
    ["FaLightbulb", "Seven ideas stand out", "Two to start now, two to build a pipeline for, three to hold as strategic options. All reuse the tower playbook on a new asset."],
  ];
  for (let i = 0; i < 4; i++) {
    const x = 0.6 + i * 3.08;
    card(s, x, 1.55, 2.85, 4.4, HEX.lt2, "Summary card " + (i + 1));
    await iconCircle(s, exec[i][0], x + 0.3, 1.85, 0.8, HEX.dk2, HEX.accent1, exec[i][1]);
    txt(s, exec[i][1], { x: x + 0.3, y: 2.9, w: 2.3, h: 0.8, fontSize: 18, bold: true, color: C.text2, fontFace: THEME.headFontFace });
    txt(s, exec[i][2], { x: x + 0.3, y: 3.75, w: 2.3, h: 2.5, fontSize: 14, color: C.text1 });
  }
  s.addNotes("Key message: the tower business is still excellent but no longer a growth engine. The best replacement candidates apply the same playbook (shared asset, long contracts, acquired scale) to new assets.");

  // ================================================================ Section 1
  pres.addSection({ title: "Why tower growth is slowing" });
  s = pres.addSlide({ masterName: "SECTION", sectionTitle: "Why tower growth is slowing" });
  s.addText("Why tower growth is slowing", { placeholder: "title" });
  s.addText("Part 1  |  The model, what carriers buy, and the headwinds", { placeholder: "body" });

  // ---------------- 3. Why the model worked
  const S1 = "Why tower growth is slowing";
  s = content("Shared steel became a 67% margin business", S1);
  s.addChart(pres.charts.BAR, [{ name: "Tower ROI", labels: ["1 tenant", "2 tenants", "3 tenants"], values: [3, 13, 24] }], {
    x: 0.6, y: 1.5, w: 5.4, h: 4.9, barDir: "col", chartColors: [HEX.accent1],
    showTitle: true, title: "Illustrative tower ROI by number of tenants (%)", titleFontSize: 14, titleColor: HEX.dk2, titleFontFace: "+mn-lt",
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 14, dataLabelColor: HEX.dk2, dataLabelFontFace: "+mn-lt", dataLabelFormatCode: '0"%"',
    catAxisLabelColor: HEX.accent3, valAxisLabelColor: HEX.accent3, catAxisLabelFontSize: 12, valAxisLabelFontSize: 11, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
    valGridLine: { color: "DCE1E7", size: 0.5 }, catGridLine: { style: "none" }, showLegend: false, valAxisHidden: true,
  });
  const drivers = [
    ["Near-zero marginal cost", "A second or third tenant adds rent with almost no added cost"],
    ["Long contracts", "10 to 15 years with automatic escalators and high exit penalties"],
    ["High switching cost", "Moving means new permits, works and downtime; zoning blocks rivals"],
    ["Low maintenance capex", "Steel lasts decades; the electronics belong to the tenant"],
    ["Scale was bought", "Carriers sold their towers: Verizon 11.4k (2015), AT&T 9.1k (2013), Telxius ~31k (2021)"],
    ["Margin came with time", "In 2002 American Tower traded below US$ 1; tenancy built today's margin"],
  ];
  for (let i = 0; i < drivers.length; i++) {
    const y = 1.5 + i * 0.83;
    s.addShape(pres.shapes.OVAL, { x: 6.5, y: y + 0.05, w: 0.42, h: 0.42, fill: { color: HEX.dk2 }, line: { color: HEX.dk2 }, objectName: "Driver marker " + (i + 1) });
    txt(s, String(i + 1), { x: 6.5, y: y + 0.05, w: 0.42, h: 0.42, fontSize: 14, bold: true, color: C.accent1, align: "center", valign: "middle" });
    txt(s, [{ text: drivers[i][0], options: { bold: true, color: C.text2, breakLine: true } }, { text: drivers[i][1], options: { color: C.text1 } }],
      { x: 7.1, y, w: 5.6, h: 0.8, fontSize: 14 });
  }
  source(s, "Sources: tower investing guides; SEC filings of American Tower (2015, 2021); AT&T/Crown Castle deal (2013); American Tower 2025 results (67.0% adj. EBITDA margin).");
  s.addNotes("The economics: once a tower is built, each new tenant is almost pure margin. Most of today's scale was acquired from carriers through sale-leasebacks, and the margin took two decades to build.");

  // ---------------- 4. Why carriers bought in
  s = content("Why carriers embraced tower sharing", S1);
  const why = [
    ["FaMoneyBillWave", "Unlock capital", "Selling towers funds spectrum, debt reduction and network upgrades"],
    ["FaBullseye", "Focus on the core", "Customers, spectrum and network, not land, permits and power"],
    ["FaPercent", "Lower cost", "Energy, ground rent and maintenance split among competitors"],
    ["FaRocket", "Faster rollout", "Existing sites in weeks instead of months of permits; sharing cuts 5G rollout cost by 40%+"],
    ["FaBalanceScale", "Better financials", "Capex turns into opex, sold at high EBITDA multiples"],
    ["FaLandmark", "Regulatory push", "Mandatory sharing rules and hard-to-permit new towers favor incumbents"],
  ];
  for (let i = 0; i < 6; i++) {
    const col = i % 3, row = Math.floor(i / 3);
    const x = 0.6 + col * 4.1, y = 1.55 + row * 2.55;
    card(s, x, y, 3.85, 2.3, HEX.lt2, "Benefit card " + (i + 1));
    await iconCircle(s, why[i][0], x + 0.25, y + 0.25, 0.7, HEX.accent2, "FFFFFF", why[i][1]);
    txt(s, why[i][1], { x: x + 1.1, y: y + 0.3, w: 2.6, h: 0.6, fontSize: 18, bold: true, color: C.text2, fontFace: THEME.headFontFace, valign: "middle" });
    txt(s, why[i][2], { x: x + 0.25, y: y + 1.1, w: 3.35, h: 1.1, fontSize: 14, color: C.text1 });
  }
  s.addNotes("These benefits still exist. What changed is the number of carriers, how much they share among themselves, and their capex cycle.");

  // ---------------- 5. Headwinds
  s = content("Eight forces are slowing tower growth", S1);
  const heads = [
    ["FaCompressArrowsAlt", "Carrier consolidation", "Fewer carriers means fewer tenants per tower; overlapping sites are shut down"],
    ["FaShareAlt", "Active RAN sharing", "Carriers now share radios, not just steel: two operators, one paying site"],
    ["FaChartLine", "End of the 5G capex wave", "Global telecom capex fell in 2023 and is forecast down ~2% in 2026"],
    ["FaHandHoldingUsd", "Pricing pressure", "Carriers say site budgets are frozen and push to renegotiate rents and escalators"],
    ["FaExclamationTriangle", "Tenant defaults", "DISH/EchoStar sold its spectrum and stopped paying; towercos are in court"],
    ["FaPercent", "Higher interest rates", "Leveraged, bond-like businesses lose valuation when rates rise"],
    ["FaSatellite", "Satellite direct-to-device", "Threatens single-tenant rural towers in the long run"],
    ["FaSearchDollar", "Fewer portfolios to buy", "Most carrier towers are already sold; M&A-led growth is plateauing"],
  ];
  for (let i = 0; i < 8; i++) {
    const col = i % 2, row = Math.floor(i / 2);
    const x = 0.6 + col * 6.15, y = 1.45 + row * 1.32;
    await iconCircle(s, heads[i][0], x, y + 0.1, 0.7, HEX.accent4, "FFFFFF", heads[i][1]);
    txt(s, [{ text: heads[i][1], options: { bold: true, color: C.text2, breakLine: true } }, { text: heads[i][2], options: { color: C.text1 } }],
      { x: x + 0.9, y: y + 0.05, w: 5.0, h: 1.2, fontSize: 14 });
  }
  source(s, "Sources: Dell'Oro (2023 capex); TelecomLead (2026 capex); Teletime (carrier statements); DCD and Bloomberg (DISH litigation); TowerXchange.");
  s.addNotes("Consolidation and RAN sharing are structural: they permanently reduce tenants per site. The capex cycle and interest rates are cyclical. DISH shows that long contracts protect less than assumed when a tenant exits.");

  // ---------------- 6. Evidence
  s = content("The numbers confirm the slowdown", S1);
  s.addChart(pres.charts.BAR, [{ name: "Share price change", labels: ["S&P 500", "American Tower", "Crown Castle", "SBA"], values: [49, -2, -22, -29] }], {
    x: 0.6, y: 1.5, w: 7.2, h: 4.9, barDir: "bar", chartColors: [HEX.accent2, HEX.accent4, HEX.accent4, HEX.accent4], invertedColors: [HEX.accent4],
    showTitle: true, title: "Share price change since end of 2022 (%)", titleFontSize: 14, titleColor: HEX.dk2, titleFontFace: "+mn-lt",
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 14, dataLabelColor: HEX.dk2, dataLabelFontFace: "+mn-lt", dataLabelFormatCode: '0"%"',
    catAxisLabelColor: HEX.dk2, valAxisLabelColor: HEX.accent3, catAxisLabelFontSize: 13, valAxisLabelFontSize: 11, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
    valGridLine: { color: "DCE1E7", size: 0.5 }, catGridLine: { style: "none" }, showLegend: false, valAxisHidden: true, catAxisLabelPos: "low",
  });
  const stats = [["~4% to 0.5%", "American Tower 2026 organic growth, before and after removing DISH leases"], ["US$ 220M", "Crown Castle 2026 revenue hit from DISH terminations"], ["−2%", "Forecast change in global telecom capex in 2026"]];
  for (let i = 0; i < 3; i++) {
    const y = 1.5 + i * 1.65;
    card(s, 8.2, y, 4.5, 1.45, HEX.lt2, "Stat card " + (i + 1));
    txt(s, stats[i][0], { x: 8.45, y: y + 0.15, w: 4.0, h: 0.6, fontSize: 30, bold: true, color: C.accent4, fontFace: THEME.headFontFace });
    txt(s, stats[i][1], { x: 8.45, y: y + 0.8, w: 4.0, h: 0.6, fontSize: 13, color: C.text1 });
  }
  source(s, "Sources: Simply Safe Dividends (share prices, 2025); Inside Towers and Wireless Estimator (2026 guidance); TelecomLead (capex).");
  s.addNotes("Since end-2022 the three US tower REITs lagged the market badly. 2026 guidance shows organic growth close to zero once DISH is removed.");

  // ---------------- 7. 5G reality
  s = content("5G kept the tower, but new demand moved on", S1);
  card(s, 0.6, 1.5, 5.6, 4.95, HEX.dk2, "What 5G did panel");
  txt(s, "What actually happened", { x: 0.9, y: 1.75, w: 5.0, h: 0.5, fontSize: 20, bold: true, color: C.accent1, fontFace: THEME.headFontFace });
  txt(s, [
    { text: "Mid-band 5G (C-band) runs on existing macro towers: the last big amendment wave", options: { bullet: true, breakLine: true } },
    { text: "mmWave and outdoor small cells did not scale as forecast", options: { bullet: true, breakLine: true } },
    { text: "Crown Castle sold 115k small cells and its fiber for US$ 8.5B to become a pure tower company", options: { bullet: true } },
  ], { x: 0.9, y: 2.4, w: 5.0, h: 3.8, fontSize: 16, color: C.background1, paraSpaceAfter: 10 });
  txt(s, "Where new demand is going", { x: 6.6, y: 1.5, w: 6.0, h: 0.5, fontSize: 20, bold: true, color: C.text2, fontFace: THEME.headFontFace });
  const demand = [
    ["FaBolt", "Energy", "Up to 32% of 5G network cost, from 23% in 4G"],
    ["FaBuilding", "Indoor coverage", "In-building wireless: ~US$ 25B (2026) to ~US$ 48B (2032)"],
    ["FaMicrochip", "Edge and AI at sites", "AI-RAN: US$ 200B+ cumulative by 2030; T-Mobile retrofitting 13k sites"],
    ["FaSatellite", "Satellite integration", "LEO direct-to-device needs gateways with land, power and fiber"],
  ];
  for (let i = 0; i < 4; i++) {
    const y = 2.15 + i * 1.08;
    await iconCircle(s, demand[i][0], 6.6, y, 0.7, HEX.accent1, HEX.dk2, demand[i][1]);
    txt(s, [{ text: demand[i][1], options: { bold: true, color: C.text2, breakLine: true } }, { text: demand[i][2], options: { color: C.text1 } }],
      { x: 7.5, y: y - 0.02, w: 5.2, h: 0.95, fontSize: 14 });
  }
  source(s, "Sources: GSMA (energy share of TCO); MarketsandMarkets (in-building wireless); Omdia and Nvidia GTC 2026 (AI-RAN); DCD (Crown Castle sale).");
  s.addNotes("The premise that 5G needs fewer tall towers is only partly true. The tower stays, but growth from it is ending, and the new money is in energy, indoor, edge compute and satellite.");

  // ================================================================ Section 2
  const S2 = "The growth ambition";
  pres.addSection({ title: S2 });
  s = pres.addSlide({ masterName: "SECTION", sectionTitle: S2 });
  s.addText("The growth ambition", { placeholder: "title" });
  s.addText("Part 2  |  What a new engine has to deliver", { placeholder: "body" });

  // ---------------- 8. Ambition ladder
  s = content("The target rises in steps through year 12", S2);
  s.addChart(pres.charts.BAR, [
    { name: "Core tower business", labels: ["Year 3", "Year 5", "Year 8", "Year 12"], values: [3.10, 3.30, 3.62, 4.07] },
    { name: "New business target", labels: ["Year 3", "Year 5", "Year 8", "Year 12"], values: [0.17, 0.51, 1.57, 4.07] },
  ], {
    x: 0.6, y: 1.5, w: 7.4, h: 4.9, barDir: "col", barGrouping: "clustered", chartColors: [HEX.accent5, HEX.accent1],
    showTitle: true, title: "Annual revenue, US$ billions (SBA scale)", titleFontSize: 14, titleColor: HEX.dk2, titleFontFace: "+mn-lt",
    showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelColor: HEX.dk2, dataLabelFontFace: "+mn-lt", dataLabelFormatCode: "0.0#",
    catAxisLabelColor: HEX.dk2, valAxisLabelColor: HEX.accent3, catAxisLabelFontSize: 13, valAxisLabelFontSize: 11, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt",
    valGridLine: { color: "DCE1E7", size: 0.5 }, catGridLine: { style: "none" }, valAxisHidden: true,
    showLegend: true, legendPos: "b", legendFontSize: 12, legendColor: HEX.dk2, legendFontFace: "+mn-lt",
  });
  const steps = [
    ["Year 3  |  Traction", "~US$ 170M, 25%+ margin, 2+ payers on the same asset"],
    ["Year 5  |  Growth engine", "~US$ 510M, 45%+ margin: more new revenue than the core adds"],
    ["Year 8  |  Second pillar", "~US$ 1.6B, 55%+ margin, tower-like cash conversion"],
    ["Year 12  |  Parity", "~US$ 4.1B, 62 to 65% margin: the main business"],
  ];
  for (let i = 0; i < 4; i++) {
    const y = 1.5 + i * 1.12;
    card(s, 8.4, y, 4.3, 0.98, i === 1 ? HEX.dk2 : HEX.lt2, "Step card " + (i + 1));
    txt(s, [{ text: steps[i][0], options: { bold: true, color: i === 1 ? C.accent1 : C.text2, breakLine: true } }, { text: steps[i][1], options: { color: i === 1 ? C.background1 : C.text1 } }],
      { x: 8.6, y: y + 0.1, w: 3.95, h: 0.85, fontSize: 13 });
  }
  source(s, "Core grows ~3% a year from ~US$ 2.85B (SBA 2026 guidance). Margin targets are EBITDA. Year 5 is the selection gate; later steps measure long-term potential.");
  s.addNotes("Building a business as large as one that took 30 years is unrealistic in 5. So the target rises in steps. The decisive one is year 5: about half a billion dollars of revenue with 45% or more EBITDA margin.");

  // ---------------- 9. Tension
  s = content("The core tension: tower-like margins or real scale", S2);
  const ten = [
    { x: 0.6, fill: HEX.lt2, head: "Ideas that reuse what we have", tc: C.text2, bc: C.text1, ic: "FaBroadcastTower", circ: HEX.accent2,
      pts: ["Tower-like margins: shared asset, low capex", "Fit with our sites, customers and skills", "But smaller revenue pools, at least at first"] },
    { x: 6.8, fill: HEX.dk2, head: "Ideas that reach scale", tc: C.accent1, bc: C.background1, ic: "FaIndustry", circ: HEX.accent1,
      pts: ["Large markets: energy, AI compute, carrier real estate", "Fast scale through acquisitions", "But heavier capital and margins closer to data centers"] },
  ];
  for (const t of ten) {
    card(s, t.x, 1.55, 5.95, 3.9, t.fill, t.head + " panel");
    await iconCircle(s, t.ic, t.x + 0.35, 1.85, 0.8, t.circ, t.fill === HEX.dk2 ? HEX.dk2 : "FFFFFF", t.head);
    txt(s, t.head, { x: t.x + 1.4, y: 1.95, w: 4.3, h: 0.6, fontSize: 20, bold: true, color: t.tc, fontFace: THEME.headFontFace, valign: "middle" });
    txt(s, t.pts.map((p, i) => ({ text: p, options: { bullet: true, breakLine: i < t.pts.length - 1 } })), { x: t.x + 0.35, y: 2.95, w: 5.3, h: 2.3, fontSize: 16, color: t.bc, paraSpaceAfter: 10 });
  }
  card(s, 0.6, 5.7, 12.15, 0.9, HEX.accent1, "Implication banner");
  txt(s, "The winning models combine both: apply the tower playbook (shared asset, long contracts, acquired scale) to a new asset class.", { x: 0.9, y: 5.8, w: 11.6, h: 0.7, fontSize: 16, bold: true, color: C.text2, valign: "middle" });
  s.addNotes("Across 13 ideas tested, this tension appeared every time. The most promising ideas are those that close the gap: they reuse our assets and customers and have a credible route to scale through acquisition.");

  // ================================================================ Section 3
  const S3 = "Ideas with transformative potential";
  pres.addSection({ title: S3 });
  s = pres.addSlide({ masterName: "SECTION", sectionTitle: S3 });
  s.addText("Ideas with transformative potential", { placeholder: "title" });
  s.addText("Part 3  |  Seven ideas, in order of priority", { placeholder: "body" });

  // ---------------- 10. Prioritization map
  s = content("Seven ideas, prioritized by potential and fit", S3);
  // axes
  card(s, 1.3, 1.45, 7.2, 4.75, HEX.lt2, "Matrix background");
  s.addShape(pres.shapes.LINE, { x: 1.3, y: 3.8, w: 7.2, h: 0, line: { color: "B8C2CC", width: 1, dashType: "dash" }, objectName: "Matrix horizontal divider" });
  s.addShape(pres.shapes.LINE, { x: 4.9, y: 1.45, w: 0, h: 4.75, line: { color: "B8C2CC", width: 1, dashType: "dash" }, objectName: "Matrix vertical divider" });
  txt(s, "Transformative potential", { x: 0.05, y: 3.6, w: 1.6, h: 0.4, fontSize: 12, bold: true, color: C.accent3, rotate: 270, align: "center" });
  txt(s, "Fit with current assets and customers", { x: 1.3, y: 6.27, w: 7.2, h: 0.3, fontSize: 12, bold: true, color: C.accent3, align: "center" });
  const pts = [
    { n: 1, x: 7.3, y: 3.15, t: 1 }, { n: 2, x: 6.5, y: 2.0, t: 1 }, { n: 3, x: 5.4, y: 2.75, t: 2 }, { n: 4, x: 3.6, y: 1.8, t: 2 },
    { n: 5, x: 2.7, y: 2.55, t: 3 }, { n: 6, x: 2.0, y: 3.35, t: 3 }, { n: 7, x: 5.7, y: 4.6, t: 3 },
  ];
  const tierCol = { 1: HEX.accent1, 2: HEX.accent2, 3: HEX.accent5 };
  for (const p of pts) {
    s.addShape(pres.shapes.OVAL, { x: p.x, y: p.y, w: 0.6, h: 0.6, fill: { color: tierCol[p.t] }, line: { color: "FFFFFF", width: 1.5 }, objectName: "Idea marker " + p.n });
    txt(s, String(p.n), { x: p.x, y: p.y, w: 0.6, h: 0.6, fontSize: 16, bold: true, color: C.text2, align: "center", valign: "middle" });
  }
  const list = [
    [1, 1, "Site platform: second payer on our sites"], [2, 1, "Energy-as-a-Service with shared batteries"],
    [3, 2, "Carrier real-estate sale-leaseback (HubCo)"], [4, 2, "Powered land for AI and the grid"],
    [5, 3, "Acquire a multi-tenant platform"], [6, 3, "Edge and AI-RAN compute hubs"], [7, 3, "Neutral satellite gateways"],
  ];
  const tierName = { 1: "Start now", 2: "Build a pipeline", 3: "Strategic options" };
  let yy = 1.45, lastT = 0;
  for (const [n, t, name] of list) {
    if (t !== lastT) { txt(s, tierName[t], { x: 8.9, y: yy, w: 3.8, h: 0.35, fontSize: 14, bold: true, color: C.accent3 }); yy += 0.4; lastT = t; }
    s.addShape(pres.shapes.OVAL, { x: 8.9, y: yy + 0.04, w: 0.36, h: 0.36, fill: { color: tierCol[t] }, line: { color: tierCol[t] }, objectName: "Legend marker " + n });
    txt(s, String(n), { x: 8.9, y: yy + 0.04, w: 0.36, h: 0.36, fontSize: 12, bold: true, color: C.text2, align: "center", valign: "middle" });
    txt(s, name, { x: 9.4, y: yy, w: 3.4, h: 0.45, fontSize: 13, color: C.text1, valign: "middle" });
    yy += 0.5;
  }
  source(s, "Priority reflects the potential to scale, closeness to the core and the strength of the tower-like economics, as tested in three rounds of structured review.");
  s.addNotes("Tier 1 ideas reuse what we already have and can start with low capital. Tier 2 ideas need an acquisition pipeline. Tier 3 ideas are options to keep warm.");

  // ---------------- idea one-pagers
  async function ideaSlide(num, tier, title, ic, what, payers, revenue, why, potential, firstStep, srcText) {
    const sl = content(title, S3);
    card(sl, 0.6, 1.45, 7.55, 5.05, HEX.lt2, "Idea description panel");
    await iconCircle(sl, ic, 0.9, 1.7, 0.85, tierCol[tier], HEX.dk2, title);
    txt(sl, [{ text: `Idea ${num}  |  ${tierName[tier]}`, options: { bold: true, color: C.accent3, breakLine: true } }, { text: "What it is", options: { bold: true, color: C.text2, fontSize: 18 } }],
      { x: 1.95, y: 1.75, w: 5.9, h: 0.8, fontSize: 13 });
    txt(sl, what, { x: 0.9, y: 2.75, w: 7.0, h: 1.55, fontSize: 15, color: C.text1 });
    txt(sl, "Who pays", { x: 0.9, y: 4.35, w: 3.3, h: 0.35, fontSize: 15, bold: true, color: C.text2 });
    txt(sl, payers, { x: 0.9, y: 4.72, w: 3.3, h: 1.7, fontSize: 13, color: C.text1 });
    txt(sl, "How it earns", { x: 4.45, y: 4.35, w: 3.4, h: 0.35, fontSize: 15, bold: true, color: C.text2 });
    txt(sl, revenue, { x: 4.45, y: 4.72, w: 3.45, h: 1.7, fontSize: 13, color: C.text1 });
    card(sl, 8.45, 1.45, 4.3, 5.05, HEX.dk2, "Why it matters panel");
    txt(sl, "Why us", { x: 8.75, y: 1.7, w: 3.7, h: 0.4, fontSize: 16, bold: true, color: C.accent1 });
    txt(sl, why, { x: 8.75, y: 2.1, w: 3.75, h: 1.3, fontSize: 13, color: C.background1 });
    txt(sl, "Why it can transform", { x: 8.75, y: 3.45, w: 3.7, h: 0.4, fontSize: 16, bold: true, color: C.accent1 });
    txt(sl, potential, { x: 8.75, y: 3.85, w: 3.75, h: 1.3, fontSize: 13, color: C.background1 });
    txt(sl, "First step", { x: 8.75, y: 5.2, w: 3.7, h: 0.4, fontSize: 16, bold: true, color: C.accent1 });
    txt(sl, firstStep, { x: 8.75, y: 5.6, w: 3.75, h: 0.85, fontSize: 13, color: C.background1 });
    if (srcText) source(sl, srcText);
    return sl;
  }

  s = await ideaSlide(1, 1, "Idea 1: Our sites as a multi-industry platform", "FaLayerGroup",
    "Sell ground space, structure space and power capacity on our ~44k existing sites to payers that are not mobile carriers, under 10 to 15 year master agreements with escalators. The tenant installs and owns its equipment, exactly as on a tower.",
    "Utilities (grid automation, smart metering), battery storage developers, private networks (agribusiness, mining, ports), satellite operators",
    "Ground lease and capacity fees per site, power upgrade fees, master agreements by industry",
    "Land, permits, access, security and grid connection already paid for and in place across the Americas and Africa",
    "Recreates the core economics (a second payer on a paid-for asset) with near-zero capex, and turns satellite from a threat into a tenant",
    "Precedent study, then 3 pilot agreements: a utility, a storage developer, a satellite operator",
    null);
  s.addNotes("This is the purest tower economics of all ideas: no purchase price, minimal capex, high incremental margin. Its revenue pool starts small, so it is the foundation, not the whole answer.");

  s = await ideaSlide(2, 1, "Idea 2: Energy-as-a-Service with shared batteries", "FaBolt",
    "Buy the carriers' captive backup batteries and generators (sale-leaseback), upgrade them to lithium and sell backup-as-a-service with an availability SLA. Aggregate tens of thousands of sites into a virtual power plant that sells capacity and grid services.",
    "Carriers (backup service fee), grid operators and utilities (capacity and ancillary services), storage aggregators",
    "10-year backup-as-a-service fee per site, 7 to 15 year capacity contracts, grid services revenue",
    "We already manage power at scale on every site and have master agreements with every carrier",
    "Attacks the carriers' biggest controllable cost (energy, up to 32% of 5G network cost) and one of the largest revenue pools tested",
    "Pilot with one carrier in one market: buy the backup fleet of ~500 sites and register it as a grid resource",
    "Source: GSMA, 5G-era mobile network cost evolution (energy share of TCO).");
  s.addNotes("Energy is the operators' number one pain. Owning the battery layer makes us their energy partner and opens a second customer, the grid, for the same asset.");

  s = await ideaSlide(3, 2, "Idea 3: Carrier real-estate sale-leaseback (HubCo)", "FaBuilding",
    "Buy the carriers' technical buildings (switching centers, aggregation hubs), lease back only the space and power they use for 15 years, and turn the rest into neutral interconnection and edge capacity. Optionally extend the playbook to utility poles and street lighting.",
    "Selling carrier (anchor lease), other carriers, regional fiber providers, CDNs, cloud and AI inference providers",
    "Rent per kW and per rack, interconnection fees, 15-year anchor lease with escalators",
    "Sale-leaseback is our core skill; SBA just did it with Millicom (~7.1k towers, ~US$ 1B) and holds the relationship",
    "The same move that built tower scale, applied to a new captive asset that carriers want to monetize as networks virtualize",
    "Map captive building portfolios with existing carrier partners and price one portfolio, starting with Millicom",
    "Sources: SBA 2025 filings (Millicom transaction); American Tower/CoreSite and Telxius precedents.");
  s.addNotes("This was rated the strongest idea from a fit perspective in the second round: current customer, clear acquisition route. It is a pipeline play: value depends on buying well.");

  s = await ideaSlide(4, 2, "Idea 4: Powered land for AI and the grid", "FaPlug",
    "Acquire captive grid connections and powered land, and lease megawatts of connected capacity to several tenants: data centers, grid batteries and electric fleet depots. Deliver land, power, basic cooling, security and fiber; tenants bring their own equipment.",
    "Hyperscalers and AI neoclouds, battery storage developers, electric bus and logistics fleets",
    "Rent per contracted kW over 10 to 15 years, interconnection fees, ground leases",
    "Infrastructure M&A, energy management, long-dated capital and a global footprint",
    "Grid connections are the new scarce permit, as tower zoning once was; AI demand makes this the largest market tested",
    "Screen 2 to 3 acquirable platforms with contracted capacity, and a joint venture partner for capital",
    "Context: data-center vacancy near record lows and multi-year grid connection queues (preliminary research).");
  s.addNotes("This idea has the largest upside but the heaviest capital. The tower lesson that applies is scarcity: whoever controls connected, permitted land controls the market.");

  // ---------------- tier 3 options
  s = content("Strategic options to keep warm", S3);
  const opts = [
    ["FaHandshake", "5. Acquire a multi-tenant platform", "Buy a ready-made platform (data center, fiber or indoor network) with existing tenants, as American Tower did with CoreSite. The fastest route to scale."],
    ["FaMicrochip", "6. Edge and AI-RAN compute hubs", "Prefabricated powered pods at aggregation sites for AI inference and AI-RAN, leased per kW. An option on the 6G cycle around 2030."],
    ["FaSatellite", "7. Neutral satellite gateways", "Shared ground stations for LEO and direct-to-device constellations on large sites, rented per antenna position. A hedge against satellite disruption."],
  ];
  for (let i = 0; i < 3; i++) {
    const x = 0.6 + i * 4.1;
    card(s, x, 1.5, 3.85, 4.9, HEX.lt2, "Option card " + (i + 5));
    await iconCircle(s, opts[i][0], x + 0.3, 1.8, 0.9, HEX.accent5, HEX.dk2, opts[i][1]);
    txt(s, opts[i][1], { x: x + 0.3, y: 2.95, w: 3.3, h: 0.9, fontSize: 18, bold: true, color: C.text2, fontFace: THEME.headFontFace });
    txt(s, opts[i][2], { x: x + 0.3, y: 3.9, w: 3.3, h: 2.4, fontSize: 14, color: C.text1 });
  }
  source(s, "Sources: American Tower (CoreSite, 2021); Nokia and Nvidia AI-RAN plans (2026); KSAT and AWS Ground Station as shared-gateway precedents.");
  s.addNotes("These options need either a large acquisition or a technology cycle that is still forming. Keep them on the radar with low-cost monitoring and partnerships.");

  // ================================================================ Closing
  pres.addSection({ title: "Next steps" });
  s = content("Recommended path", "Next steps");
  const phases = [
    ["0 to 12 months", "Prove", ["Site platform: 3 pilot master agreements", "Energy-as-a-Service: 500-site pilot", "Build the captive-asset map"]],
    ["12 to 36 months", "Scale", ["Roll out the winning pilots across markets", "First HubCo portfolio, starting with Millicom", "Partner and capital for powered land"]],
    ["36+ months", "Transform", ["Platform acquisition if the pilots confirm demand", "Edge and AI-RAN hubs with the 6G cycle", "Satellite gateways as constellations scale"]],
  ];
  for (let i = 0; i < 3; i++) {
    const x = 0.6 + i * 4.1;
    card(s, x, 1.55, 3.85, 3.6, i === 0 ? HEX.dk2 : HEX.lt2, "Phase card " + (i + 1));
    const dark = i === 0;
    txt(s, phases[i][0], { x: x + 0.3, y: 1.8, w: 3.3, h: 0.4, fontSize: 14, bold: true, color: dark ? C.accent5 : C.accent3 });
    txt(s, phases[i][1], { x: x + 0.3, y: 2.2, w: 3.3, h: 0.7, fontSize: 28, bold: true, color: dark ? C.accent1 : C.text2, fontFace: THEME.headFontFace });
    txt(s, phases[i][2].map((p, j) => ({ text: p, options: { bullet: true, breakLine: j < 2 } })), { x: x + 0.3, y: 3.05, w: 3.3, h: 1.95, fontSize: 15, color: dark ? C.background1 : C.text1, paraSpaceAfter: 10 });
    if (i < 2) s.addShape(pres.shapes.CHEVRON, { x: x + 3.88, y: 3.12, w: 0.2, h: 0.45, fill: { color: HEX.accent1 }, line: { color: HEX.accent1 }, objectName: "Phase arrow " + (i + 1) });
  }
  card(s, 0.6, 5.45, 12.15, 1.0, HEX.accent1, "Gate banner");
  await iconCircle(s, "FaCheckCircle", 0.85, 5.6, 0.7, HEX.dk2, HEX.accent1, "Evidence gate");
  txt(s, "Each step is gated by real-world evidence: signed pilots, measured margins and a repeatable deployment cost. Large capital only follows proof.", { x: 1.8, y: 5.55, w: 10.7, h: 0.8, fontSize: 16, bold: true, color: C.text2, valign: "middle" });
  s.addNotes("Start with the two low-capital ideas to prove demand and margins, build the acquisition pipeline in parallel, and only commit large capital once pilots confirm the economics.");

  s = pres.addSlide({ masterName: "TITLE", sectionTitle: "Next steps" });
  s.addText("Apply the tower playbook to a new asset", { placeholder: "title" });
  s.addText("Shared asset  |  Long contracts  |  Acquired scale", { placeholder: "body" });
  s.addNotes("Closing message.");

  // ---------------- Sources
  s = content("Sources", "Next steps");
  txt(s, [
    "American Tower, Q4 and full-year 2025 results; SEC filings on Verizon (2015) and Telxius (2021) transactions",
    "SBA Communications, Q4 2025 results and 2026 outlook; 2025 10-Q; Millicom transaction (2025)",
    "Crown Castle: sale of fiber and small cells to Zayo and EQT (DCD, Wireless Estimator, 2025 to 2026)",
    "Inside Towers and Wireless Estimator: 2026 tower organic growth and DISH/EchoStar terminations",
    "Simply Safe Dividends: tower REIT share performance since 2022; Barclays downgrade (Yahoo Finance)",
    "Dell'Oro (telecom capex 2023, RAN market 2026); TelecomLead (2026 capex outlook)",
    "GSMA: 5G-era mobile network cost evolution; MarketsandMarkets: in-building wireless market",
    "Nvidia GTC 2026 and Nokia AI-RAN announcements; Omdia AI-RAN forecast",
    "Norton Rose Fulbright, Capacity and TowerXchange: tower sharing models and sale-leaseback structures",
    "Christensen; O'Reilly and Tushman; Zook (Bain); McKinsey business-building research",
  ].map((t, i, a) => ({ text: t, options: { bullet: true, breakLine: i < a.length - 1 } })), { x: 0.6, y: 1.5, w: 12.1, h: 5.2, fontSize: 14, color: C.text1, paraSpaceAfter: 6 });

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("wrote", OUT);
})();
