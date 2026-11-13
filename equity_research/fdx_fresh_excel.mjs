import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [, , inputPath, outputPath, moduleDirectory, previewDirectory] = process.argv;
const require = createRequire(path.join(path.resolve(moduleDirectory), "package.json"));
const { SpreadsheetFile, Workbook } = await import(pathToFileURL(require.resolve("@oai/artifact-tool")).href);
const plan = JSON.parse(await fs.readFile(inputPath, "utf8"));
const { navy = "#17365D", pale = "#DDEBF7", editable = "#FFF2CC", font = "Calibri" } = plan.palette ?? {};
const ink = "#172332";
const linked = "#17653C";
const inputBlue = "#154DA1";
const muted = "#4E5D6B";
const numberFormat = '#,##0.0;(#,##0.0);"-"';
const formulaError = /^#(?:REF!|DIV\/0!|VALUE!|NAME\?|N\/A|NUM!|NULL!|SPILL!|CALC!|CIRC!)/;
const outputs = new Set(["Cover", "DCF", "Comps"]);
const periodSheets = new Set([
  "Assumptions", "Revenue", "Income statement", "Balance sheet", "Cash flow", "PP&E", "Debt schedule",
  "Working capital", "Equity", "Checks", "Actuals",
]);
const sheetWidths = {
  Cover: { A: 39, B: 21, C: 20, D: 20, E: 19 },
  Comps: { A: 48, B: 21, C: 21, D: 21, E: 21 },
  Sources: { A: 42, B: 18, C: 80, D: 70 },
  Checks: { A: 53 },
  Assumptions: { A: 46 },
  Actuals: { A: 48 },
};
const workbook = Workbook.create();
const sheets = new Map(Object.keys(plan.cells).map((name) => [name, workbook.worksheets.add(name)]));

function indexes(address) {
  const [, letters, row] = /^([A-Z]+)([1-9]\d*)$/.exec(address) ?? [];
  if (!letters) throw new Error(`Invalid workbook address ${address}.`);
  let column = 0;
  for (const letter of letters) column = column * 26 + letter.charCodeAt(0) - 64;
  return [Number(row) - 1, column - 1];
}

function isFormula(value) {
  return typeof value === "string" && value.startsWith("=");
}

function populate(sheet, records) {
  const positions = Object.entries(records).map(([address, record]) => [address, ...indexes(address), record]);
  const rows = 1 + Math.max(0, ...positions.map(([, row]) => row));
  const columns = 1 + Math.max(0, ...positions.map(([, , column]) => column));
  const values = Array.from({ length: rows }, () => Array(columns).fill(null));
  const formulas = Array.from({ length: rows }, () => []);
  for (const [, row, column, [value]] of positions) {
    if (isFormula(value)) {
      formulas[row].push([column, value]);
    } else {
      values[row][column] = value;
    }
  }
  const used = sheet.getRangeByIndexes(0, 0, rows, columns);
  used.values = values;
  for (const [row, entries] of formulas.entries()) {
    entries.sort(([left], [right]) => left - right);
    let index = 0;
    while (index < entries.length) {
      const start = entries[index][0];
      const run = [];
      while (index < entries.length && entries[index][0] === start + run.length) {
        run.push(entries[index][1]);
        index += 1;
      }
      sheet.getRangeByIndexes(row, start, 1, run.length).formulas = [run];
    }
  }
  return { positions, rows, columns, used };
}

function formatCover(sheet, controls) {
  const selector = sheet.getRange("B5");
  selector.dataValidation = { rule: { type: "list", values: controls } };
  selector.format.fill = editable;
  selector.format.horizontalAlignment = "center";
  selector.format.font = { name: font, size: 11, bold: true, color: inputBlue };
  selector.format.borders = { preset: "outside", style: "dashed", color: navy };
  sheet.getRange("B6:B13").format.font = { name: font, size: 12, bold: true, color: navy };
  sheet.getRange("A5:E13").format.rowHeight = 21;
}

function formatSheet(name, sheet, { positions, rows, columns, used }) {
  sheet.showGridLines = false;
  used.format.font = { name: font, size: 10, color: ink };
  used.format.rowHeight = 16;
  used.format.verticalAlignment = "center";
  used.format.columnWidth = 16;
  const widths = { A: 43, ...sheetWidths[name] };
  for (const [column, width] of Object.entries(widths)) {
    sheet.getRange(`${column}1:${column}${rows}`).format.columnWidth = width;
  }
  if (outputs.has(name)) sheet.tabColor = navy;
  if (name === "Assumptions") sheet.tabColor = "#587FA3";
  if (name === "Actuals") sheet.tabColor = "#E2D2AF";
  if (name === "Sources") sheet.tabColor = "#98ABBA";
  const numericHeader = positions.find(([, , column, [value, kind]]) =>
    column > 0 && kind === "header" && (typeof value === "number" || isFormula(value)));
  const freezeRow = { Comps: 5, Sources: 4 }[name] ?? (periodSheets.has(name) && numericHeader ? numericHeader[1] + 1 : 0);
  if (freezeRow) sheet.freezePanes.freezeRows(freezeRow);

  const occupied = new Map();
  for (const [, row, column, [value]] of positions) {
    if (value !== null && value !== "") occupied.set(row, Math.max(column, occupied.get(row) ?? 0));
  }
  const heights = new Map();
  const topBorder = { style: "thin", color: "#B2C5D9" };
  for (const [address, row, column, [value, kind, format]] of positions) {
    const cell = sheet.getRange(address);
    const columnName = address.replace(/\d+$/, "");
    const capacity = widths[columnName] ?? 16;
    const formula = isFormula(value);
    if (column === 0 && (kind === "section" || kind === "total")) {
      const band = sheet.getRangeByIndexes(row, 0, 1, columns);
      band.format.fill = kind === "section" ? navy : "#EEF4F8";
      band.format.borders = { top: topBorder };
    }
    if (format) {
      cell.setNumberFormat(format);
    } else if (typeof value === "number" || (formula && kind !== "header")) {
      cell.setNumberFormat(numberFormat);
    }
    if (kind === "title" || (address === "A1" && typeof value === "string" && !formula)) {
      cell.format.font = { name: font, size: name === "Cover" ? 18 : 15, bold: true, color: navy };
      heights.set(row, 27);
    } else if (kind === "section") {
      cell.format.fill = navy;
      cell.format.font = { name: font, size: 10, bold: true, color: "#FFFFFF" };
      heights.set(row, 20);
    } else if (kind === "header") {
      cell.format.fill = pale;
      cell.format.font = { name: font, size: 10, bold: true, color: navy };
      if (column) cell.format.horizontalAlignment = "center";
    } else if (kind === "input") {
      cell.format.fill = "#F1F6FF";
      cell.format.font = { name: font, size: 10, color: inputBlue };
    } else if (kind === "total") {
      cell.format.font = { name: font, size: 10, bold: true, color: navy };
    } else if (kind === "note" || kind === "subtitle") {
      cell.format.font = { name: font, size: 10, color: muted, italic: kind === "subtitle" };
    } else if (formula && value.includes("!") && !outputs.has(name) && name !== "Checks") {
      cell.format.font = { name: font, size: 10, color: linked };
    }
    if (formula && /^='?Cover'?\!\$?B\$?5$/i.test(value)) {
      cell.format.horizontalAlignment = "center";
      cell.format.font = { name: font, size: 10, bold: true, color: linked };
      cell.format.borders = { preset: "outside", style: "dotted", color: "#B2C5D9" };
    }
    if (kind === "check" && formula) {
      cell.setNumberFormat('[>=0.005]0.00;[<=-0.005](0.00);"0.00"');
      cell.conditionalFormats.addCustom(`IFERROR(ABS(${address})>0.01,TRUE)`,
        { fill: "#FCE8E6", font: { bold: true, color: "#A52620" } });
    }
    if (typeof value !== "string" || formula || !value) continue;
    const standalone = column === 0 && occupied.get(row) === 0 && columns > 1;
    const compNote = name === "Comps" && column === 1 && row >= 52 && row <= 56;
    const sourceNote = name === "Sources" && column === 2 && kind === "note";
    const rightmostNote = (compNote || sourceNote) && occupied.get(row) === column && columns > column + 1;
    if ((standalone && ["title", "note", "subtitle"].includes(kind)) || rightmostNote) {
      const width = Object.values(widths).slice(column, columns).reduce((total, item) => total + item, 0)
        + 16 * Math.max(0, columns - Object.keys(widths).length);
      const area = sheet.getRangeByIndexes(row, column, 1, columns - column);
      area.merge();
      if (value.length > width && kind !== "title") {
        area.format.wrapText = true;
        heights.set(row, Math.max(heights.get(row) ?? 16, 7 + Math.ceil(value.length / width) * 14));
      }
    } else if (value.length > capacity && (
      ["note", "subtitle", "header"].includes(kind) || name === "Sources" || column === 0
      || occupied.get(row) > column
    )) {
      cell.format.wrapText = true;
      heights.set(row, Math.max(heights.get(row) ?? 16, 7 + Math.ceil(value.length / capacity) * 14));
    }
  }
  for (const [row, height] of heights) sheet.getRange(`A${row + 1}`).format.rowHeight = height;
  if (name === "Cover") formatCover(sheet, Object.values(plan.scenarioControls));
  if (name === "DCF") {
    for (const address of ["F65", "F77"]) {
      if (!plan.cells.DCF[address]) continue;
      const center = sheet.getRange(address);
      center.format.fill = pale;
      center.format.font = { name: font, size: 10, bold: true, color: navy };
      center.format.borders = { preset: "outside", style: "thin", color: "#8AA6C0" };
    }
  }
}

const layouts = new Map();
for (const [name, sheet] of sheets) {
  const layout = populate(sheet, plan.cells[name]);
  formatSheet(name, sheet, layout);
  layouts.set(name, layout);
}

const selector = sheets.get("Cover").getRange("B5");

function matches(actual, expected) {
  if (typeof expected === "number") {
    return typeof actual === "number" && Number.isFinite(actual)
      && Math.abs(actual - expected) <= Math.max(1e-6, Math.abs(expected) * 1e-10);
  }
  return actual === expected || (expected == null && (actual == null || actual === ""));
}

function verifyCase(caseName, expectations) {
  selector.values = [[plan.scenarioControls[caseName]]];
  workbook.recalculate();
  const failures = [];
  let tested = 0;
  for (const name of Object.keys(expectations)) {
    if (!layouts.has(name)) failures.push(`Unexpected verification sheet ${name}.`);
  }
  for (const [name, { used, positions }] of layouts) {
    const grid = used.values;
    for (const [address, row, column, [value]] of positions) {
      const actual = grid[row]?.[column];
      if (isFormula(value) && typeof actual === "string" && formulaError.test(actual)) {
        failures.push(`${name}!${address} ${actual}`);
      }
    }
    for (const [address, expected] of Object.entries(expectations[name] ?? {})) {
      if (!Object.hasOwn(plan.cells[name], address)) failures.push(`Unexpected verification cell ${name}!${address}.`);
      const [row, column] = indexes(address);
      const actual = grid[row]?.[column];
      if (!matches(actual, expected)) {
        failures.push(`${name}!${address} returned ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}.`);
      }
      tested += 1;
    }
  }
  if (failures.length || !tested) {
    throw new Error(`${caseName} case failed verification (${failures.length} discrepancies).\n${failures.slice(0, 20).join("\n")}`);
  }
  console.log(`${caseName}: ${tested} calculated workbook values matched Python.`);
}

function verifyRecalculation({ input, changed, unchanged }) {
  const getCell = ({ sheet, address }) => sheets.get(sheet).getRange(address);
  const inputCell = getCell(input);
  const original = inputCell.values[0][0];
  const [planned, kind] = plan.cells[input.sheet][input.address];
  if (typeof planned !== "number" || kind !== "input" || !Number.isFinite(input.delta) || input.delta === 0) {
    throw new Error("The recalculation probe requires an editable numeric assumption and a nonzero adjustment.");
  }
  const movingCells = changed.map(getCell);
  const historicalCells = unchanged.map(getCell);
  const before = movingCells.map((cell) => cell.values[0][0]);
  const history = historicalCells.map((cell) => cell.values[0][0]);
  try {
    inputCell.values = [[original + input.delta]];
    workbook.recalculate();
    if (!movingCells.every((cell, index) =>
      Number.isFinite(cell.values[0][0]) && !matches(cell.values[0][0], before[index]))
      || !historicalCells.every((cell, index) => matches(cell.values[0][0], history[index]))) {
      throw new Error("The assumption edit must flow through forecast and valuation without changing historical values.");
    }
  } finally {
    inputCell.values = [[original]];
  }
  console.log("A forecast assumption edit flowed through to the valuation and preserved historical values.");
}

for (const [caseName, expectations] of Object.entries(plan.scenarioExpected)) {
  if (caseName !== plan.case) verifyCase(caseName, expectations);
}
verifyCase(plan.case, plan.expected);
if (plan.recalculationProbe) {
  const probeCase = plan.recalculationProbe.case ?? plan.case;
  if (probeCase !== plan.case) {
    selector.values = [[plan.scenarioControls[probeCase]]];
    workbook.recalculate();
  }
  verifyRecalculation(plan.recalculationProbe);
  verifyCase(plan.case, plan.expected);
}

const overview = await workbook.inspect({
  kind: "table", range: "Cover!A3:B13", include: "values,formulas", tableMaxRows: 11, tableMaxCols: 2, maxChars: 1800,
});
console.log(overview.ndjson);

async function savePreview(name, range, filename) {
  const image = await workbook.render({ sheetName: name, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDirectory, filename + ".png"), new Uint8Array(await image.arrayBuffer()));
}

if (previewDirectory) {
  await fs.mkdir(previewDirectory, { recursive: true });
  for (const [name, { rows, columns }] of layouts) {
    const lastColumn = String.fromCharCode(64 + Math.min(columns, 16));
    const lastRow = Math.min(rows, name === "Cover" ? 25 : 20);
    await savePreview(name, `A1:${lastColumn}${lastRow}`, name.replaceAll(/[^a-z0-9]+/gi, "-"));
  }
  const dcf = layouts.get("DCF");
  for (const [, row, column, [value, kind]] of dcf?.positions ?? []) {
    if (column === 0 && kind === "section" && typeof value === "string" && /sensitiv/i.test(value)) {
      const lastRow = Math.min(dcf.rows, row + 10);
      await savePreview("DCF", `A${row + 1}:I${lastRow}`, `DCF-sensitivity-${row + 1}`);
    }
  }
}
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
