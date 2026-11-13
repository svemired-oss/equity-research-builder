import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [, , inputPath, outputPath, moduleDirectory, previewDirectory] = process.argv;
const require = createRequire(path.join(path.resolve(moduleDirectory), "package.json"));
const { SpreadsheetFile, Workbook } = await import(pathToFileURL(require.resolve("@oai/artifact-tool")).href);
const plan = JSON.parse(await fs.readFile(inputPath, "utf8"));
const { navy = "#17365D", pale = "#DDEBF7", editable = "#FFF2CC", font = "Calibri" } = plan.palette ?? {};
const ink = "#182432";
const linked = "#17653C";
const inputBlue = "#0000BB";
const grey = "#586573";
const numberFormat = '#,##0.0;(#,##0.0);"-"';
const formulaErrors = /^#(?:REF!|DIV\/0!|VALUE!|NAME\?|N\/A|NUM!|NULL!|SPILL!|CALC!|CIRC!)/;
const coverWidths = { A: 36, B: 21, C: 14, D: 14, E: 14 };

const periodBands = {
  Revenue: [1, ["C1:H1", "#E8EEF4"], ["J1:N1", pale]],
  "Operating expenses": [3, ["B3:D3", "#E8EEF4"], ["E3:I3", pale],
    ["B32:D32", "#E8EEF4"], ["E32:I32", pale], ["B55:D55", "#E8EEF4"], ["E55:I55", pale]],
  "Income statement": [4, ["B4:G4", "#E8EEF4"], ["H4:L4", pale],
    ["B40:G40", "#E8EEF4"], ["H40:L40", pale]],
  "Balance sheet": [3, ["B3:F3", "#E8EEF4"], ["G3:K3", pale]],
  "Derived cash flow": [1, ["B1:B1", "#E8EEF4"], ["C1:G1", pale]],
  CapEx: [8, ["B8:E8", "#E8EEF4"], ["F8:J8", pale]],
  "Debt schedule": [6, ["B6:F6", "#E8EEF4"], ["G6:K6", pale],
    ["B26:B26", "#E8EEF4"], ["C26:G26", pale]],
};

const workbook = Workbook.create();
const sheets = new Map(Object.keys(plan.cells).map((name) => [name, workbook.worksheets.add(name)]));

function indexes(address) {
  const [, letters, row] = /^([A-Z]+)([1-9]\d*)$/.exec(address) ?? [];
  if (!letters) throw new Error(`Invalid cell address ${address}`);
  let column = 0;
  for (const letter of letters) column = column * 26 + letter.charCodeAt(0) - 64;
  return [Number(row) - 1, column - 1];
}

function isFormula(value) {
  return typeof value === "string" && value.startsWith("=");
}

function populate(sheet, cells) {
  const positions = Object.entries(cells).map(([address, record]) => [address, ...indexes(address), record]);
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

function formatCover(sheet) {
  for (const row of [1, 2, 4, 6, 8, 16]) sheet.mergeCells(`A${row}:E${row}`);
  sheet.getRange("A6:E8").format.wrapText = true;
  sheet.getRange("A6:E8").format.rowHeight = 24;
  sheet.getRange("A17:B22").format.rowHeight = 22;
  sheet.getRange("A17:B22").format.borders = { preset: "outside", style: "thin", color: navy };
  sheet.getRange("A17:B22").format.fill = pale;
  sheet.getRange("B17:B19").format.font = { name: font, size: 13, bold: true, color: navy };
}

function formatSheet(name, sheet, { positions, rows, columns, used }) {
  sheet.showGridLines = false;
  used.format.font = { name: font, size: 10, color: ink };
  used.format.rowHeight = 15;
  used.format.verticalAlignment = "center";
  used.format.columnWidth = 15;
  sheet.getRangeByIndexes(0, 0, rows, 1).format.columnWidth = 46;
  const widths = name === "Sources" ? { A: 43, B: 31, C: 38, D: 72, E: 64, F: 50 }
    : name === "Checks" ? { A: 57 }
    : name === "Comps" ? { A: 39, B: 15 }
    : name === "Cover" ? coverWidths
    : {};
  for (const [column, width] of Object.entries(widths)) {
    sheet.getRange(`${column}1:${column}${rows}`).format.columnWidth = width;
  }
  if (["Cover", "DCF", "Comps"].includes(name)) sheet.tabColor = navy;
  if (name === "Sources") sheet.tabColor = "#7996B1";
  if (name === "Comps") sheet.freezePanes.freezeRows(5);
  if (name === "Sources") sheet.freezePanes.freezeRows(4);
  const periodFills = new Map();
  if (periodBands[name]) {
    const [headerRow, ...bands] = periodBands[name];
    sheet.freezePanes.freezeRows(headerRow);
    for (const [address, fill] of bands) {
      const band = sheet.getRange(address);
      band.format.fill = fill;
      band.format.font = { name: font, size: 10, bold: true, color: navy };
      const [[row, first], [, last]] = address.split(":").map(indexes);
      for (let column = first; column <= last; column += 1) periodFills.set(`${row},${column}`, fill);
    }
  }

  const heights = new Map();
  for (const [address, row, column, [value, kind, format]] of positions) {
    const cell = sheet.getRange(address);
    if (column === 0 && kind === "section") {
      const section = sheet.getRangeByIndexes(row, 0, 1, columns);
      section.format.fill = navy;
      section.format.font = { name: font, size: 10, bold: true, color: "#FFFFFF" };
    }
    if (column === 0 && kind === "total" && name !== "Cover") {
      const total = sheet.getRangeByIndexes(row, 0, 1, columns);
      total.format.fill = "#F1F6FA";
      total.format.borders = { top: { style: "thin", color: "#B2C5D9" } };
    }
    if (format) {
      cell.setNumberFormat(format);
    } else if (kind !== "header" && (typeof value === "number" || isFormula(value))) {
      cell.setNumberFormat(numberFormat);
    }
    if (kind === "title" || (address === "A1" && typeof value === "string" && !kind)) {
      cell.format.font = { name: font, size: 16, bold: true, color: navy };
      heights.set(row, 25);
    } else if (kind === "section") {
      cell.format.fill = navy;
      cell.format.font = { name: font, size: 10, bold: true, color: "#FFFFFF" };
    } else if (kind === "header") {
      cell.format.fill = periodFills.get(`${row},${column}`) ?? pale;
      cell.format.font = { name: font, size: 10, bold: true, color: navy };
    } else if (kind === "input") {
      cell.format.fill = name === "Cover" ? editable : "#F5F9FF";
      cell.format.font = { name: font, size: 10, color: inputBlue };
    } else if (kind === "total") {
      cell.format.font = { name: font, size: 10, bold: true, color: navy };
    } else if (kind === "note" || kind === "subtitle") {
      cell.format.font = { name: font, size: 10, color: grey, italic: kind === "subtitle" };
    } else if (isFormula(value) && value.includes("!") && name !== "Checks") {
      cell.format.font = { name: font, size: 10, color: linked };
    }
    if (kind === "check" && isFormula(value)) {
      cell.conditionalFormats.addCustom(`IFERROR(ABS(${address})>0.01,TRUE)`,
        { fill: "#FCE8E6", font: { bold: true, color: "#A52620" } });
    }
    if (typeof value === "string" && !isFormula(value)
        && (["note", "subtitle", "header"].includes(kind) || /^https?:\/\//.test(value)
          || name === "Sources" || (column === 0 && value.length > 48 && kind !== "title"))) {
      const letters = address.replace(/\d+/, "");
      const capacity = widths[letters] ?? (column === 0 ? 46 : 15);
      if (value.length > capacity) {
        cell.format.wrapText = true;
        const height = 6 + Math.ceil(value.length / capacity) * 14;
        heights.set(row, Math.max(heights.get(row) ?? 15, height));
      }
    }
  }
  for (const [row, height] of heights) sheet.getRange(`A${row + 1}`).format.rowHeight = height;
  if (name === "Cover") formatCover(sheet);
  if (name === "DCF") {
    for (const address of ["F30", "F52"]) {
      if (plan.cells.DCF[address]) sheet.getRange(address).format.fill = editable;
    }
  }
}

const layouts = new Map();
for (const [name, sheet] of sheets) {
  const layout = populate(sheet, plan.cells[name]);
  layouts.set(name, layout);
  formatSheet(name, sheet, layout);
}

function verify(label, expectations) {
  workbook.recalculate();
  const failures = [];
  let count = 0;
  for (const name of Object.keys(expectations)) {
    if (!layouts.has(name)) failures.push(`Unknown validation sheet ${name}`);
  }
  for (const [name, { used, positions }] of layouts) {
    const grid = used.values;
    for (const [address, row, column, [value]] of positions) {
      const actual = grid[row]?.[column];
      if (isFormula(value) && typeof actual === "string" && formulaErrors.test(actual)) {
        failures.push(`${name}!${address} ${actual}`);
      }
    }
    for (const [address, expected] of Object.entries(expectations[name] ?? {})) {
      if (!Object.hasOwn(plan.cells[name], address)) {
        failures.push(`Unknown validation cell ${name}!${address}`);
        continue;
      }
      const [row, column] = indexes(address);
      const actual = grid[row]?.[column];
      const matches = typeof expected === "number"
        ? typeof actual === "number" && Number.isFinite(actual)
          && Math.abs(actual - expected) <= Math.max(1e-6, Math.abs(expected) * 1e-10)
        : actual === expected || (expected === null && (actual === "" || actual == null));
      if (!matches) failures.push(`${name}!${address} returned ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);
      count += 1;
    }
  }
  if (!count) failures.push("No validation cells were supplied");
  if (failures.length) {
    throw new Error(`${label}: ${failures.length} discrepancies\n${failures.slice(0, 20).join("\n")}`);
  }
  console.log(`${label}: ${count} checks against Python`);
}

verify("Base", plan.expected);
if (plan.scenarioExpected || plan.scenarioControl) {
  if (!plan.scenarioExpected || !plan.scenarioControl) throw new Error("A scenario requires a control and expected values");
  const { sheet, address, value } = plan.scenarioControl;
  const [base] = plan.cells[sheet]?.[address] ?? [];
  if (base === undefined || isFormula(base) || value === base || typeof value !== typeof base || isFormula(value)) {
    throw new Error(`Scenario must change an existing literal at ${sheet}!${address}`);
  }
  const control = sheets.get(sheet).getRange(address);
  control.values = [[value]];
  verify("Scenario", plan.scenarioExpected);
  control.values = [[base]];
  verify("Restored base", plan.expected);
}

if (previewDirectory) {
  await fs.mkdir(previewDirectory, { recursive: true });
  for (const [name, { rows, columns }] of layouts) {
    const lastColumn = String.fromCharCode(64 + (name === "Cover" ? 5 : Math.min(columns, 16)));
    const lastRow = Math.min(rows, name === "Cover" ? 27 : 22);
    const image = await workbook.render({ sheetName: name, range: `A1:${lastColumn}${lastRow}`, scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDirectory, `${name.replaceAll(/[^a-z0-9]+/gi, "-")}.png`),
      new Uint8Array(await image.arrayBuffer()));
  }
  for (const [name, range, filename] of [
    ["DCF", "A24:I55", "DCF-sensitivity"], ["Comps", "A21:O35", "Comps-valuation"],
    ["Derived cash flow", "A51:G78", "Cash-and-FCFE"], ["Revenue", "A41:N65", "Revenue-drivers"],
  ]) {
    const image = await workbook.render({ sheetName: name, range, scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDirectory, `${filename}.png`), new Uint8Array(await image.arrayBuffer()));
  }
}
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
