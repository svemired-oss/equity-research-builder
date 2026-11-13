import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const [, , inputPath, outputPath, moduleDirectory, previewDirectory] = process.argv;
const require = createRequire(path.join(path.resolve(moduleDirectory), "package.json"));
const { SpreadsheetFile, Workbook } = await import(pathToFileURL(require.resolve("@oai/artifact-tool")).href);
const plan = JSON.parse(await fs.readFile(inputPath, "utf8"));
const { navy, pale, editable, font } = plan.palette;
const ink = "#182432";
const linked = "#17653C";
const inputBlue = "#0000BB";
const grey = "#586573";
const numberFormat = '#,##0.0;(#,##0.0);"-"';
const errors = /^#(?:REF!|DIV\/0!|VALUE!|NAME\?|N\/A|NUM!|NULL!|SPILL!|CALC!|CIRC!)/;
const statements = new Set(["Revenue model", "Income statement", "Balance sheet", "Cash flow", "Equity", "PP&E", "Debt", "NWC"]);
const workbook = Workbook.create();
const sheets = new Map(Object.keys(plan.cells).map((name) => [name, workbook.worksheets.add(name)]));

function indexes(address) {
  const [, letters, row] = /^([A-Z]+)(\d+)$/.exec(address);
  let column = 0;
  for (const letter of letters) column = column * 26 + letter.charCodeAt(0) - 64;
  return [Number(row) - 1, column - 1];
}

function populate(sheet, cells) {
  const positions = Object.entries(cells).map(([address, record]) => [address, ...indexes(address), record]);
  const rows = 1 + Math.max(...positions.map(([, row]) => row));
  const cols = 1 + Math.max(...positions.map(([, , column]) => column));
  const values = Array.from({ length: rows }, () => Array(cols).fill(null));
  const formulas = Array.from({ length: rows }, () => []);
  for (const [, row, column, [value]] of positions) {
    if (typeof value === "string" && value.startsWith("=")) {
      formulas[row].push([column, value]);
    } else {
      values[row][column] = value;
    }
  }
  const used = sheet.getRangeByIndexes(0, 0, rows, cols);
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
  return { positions, rows, cols, used };
}

function formatSheet(name, sheet, layout) {
  const { positions, rows, cols, used } = layout;
  sheet.showGridLines = false;
  used.format.font = { name: font, size: 10, color: ink };
  used.format.rowHeight = 15;
  used.format.verticalAlignment = "center";
  used.format.columnWidth = 15;
  sheet.getRangeByIndexes(0, 0, rows, 1).format.columnWidth = 44;
  const band = { style: "thin", color: "#B2C5D9" };
  if (statements.has(name)) {
    const headerRow = name === "Cash flow" ? 2 : 5;
    sheet.freezePanes.freezeRows(headerRow);
    sheet.getRange("M1:N5").format.columnWidth = 3;
    sheet.getRange("O1:P5").format.columnWidth = 17;
    for (const [range, fill] of [[`A${headerRow}:F${headerRow}`, "#E8EEF4"], [`G${headerRow}:L${headerRow}`, pale],
      [`O${headerRow}:P${headerRow}`, pale]]) {
      sheet.getRange(range).format.fill = fill;
      sheet.getRange(range).format.font = { name: font, size: 10, bold: true, color: navy };
    }
    if (name !== "Revenue model") sheet.getRange(`G${headerRow}:L${headerRow}`).setNumberFormat('yyyy"E"');
  }
  if (name.endsWith("inputs")) sheet.tabColor = "#7996B1";
  if (["Cover", "DCF", "Comps"].includes(name)) sheet.tabColor = navy;

  const totalRows = new Set();
  for (const [address, row, column, [, kind]] of positions) {
    if (column === 0 && kind === "total" && name !== "Cover") totalRows.add(row);
    if (column === 0 && kind === "section") {
      const section = sheet.getRangeByIndexes(row, 0, 1, cols);
      section.format.fill = navy;
      section.format.font = { name: font, size: 10, bold: true, color: "#FFFFFF" };
    }
    if (address === "A1") sheet.getRange(address).format.rowHeight = 23;
  }
  for (const row of totalRows) {
    const total = sheet.getRangeByIndexes(row, 0, 1, cols);
    total.format.fill = "#F1F6FA";
    total.format.borders = { top: band };
  }

  for (const [address, , , [value, kind, fmt]] of positions) {
    const cell = sheet.getRange(address);
    const formula = typeof value === "string" && value.startsWith("=");
    if (statements.has(name) && name !== "Revenue model" && /^[G-L]([25])$/.test(address) && fmt === "yyyy") {
      cell.setNumberFormat('yyyy"E"');
    } else if (fmt) {
      cell.setNumberFormat(fmt);
    } else if ((formula || typeof value === "number") && kind !== "header") {
      cell.setNumberFormat(numberFormat);
    }
    if (kind === "title" || address === "A1") {
      cell.format.font = { name: font, size: 15, bold: true, color: navy };
    } else if (kind === "section") {
      cell.format.font = { name: font, size: 10, bold: true, color: "#FFFFFF" };
      cell.format.fill = navy;
    } else if (kind === "header") {
      cell.format.fill = pale;
      cell.format.font = { name: font, size: 10, bold: true, color: navy };
    } else if (kind === "input") {
      cell.format.font = { name: font, size: 10, color: inputBlue };
      cell.format.fill = "#F5F9FF";
    } else if (kind === "note" || kind === "subtitle") {
      cell.format.font = { name: font, size: 10, color: grey, italic: kind === "subtitle" };
    } else if (kind === "total") {
      cell.format.font = { name: font, size: 10, bold: true, color: navy };
    } else if (formula && value.includes("!") && name !== "Checks") {
      cell.format.font = { name: font, size: 10, color: linked };
    }
  }
  if (name === "Cover") formatCover(sheet);
  if (name === "Sources") {
    sheet.getRange("B1:B60").format.columnWidth = 18;
    sheet.getRange("C1:C60").format.columnWidth = 21;
    sheet.getRange("D1:D60").format.columnWidth = 77;
    sheet.getRange("E1:E60").format.columnWidth = 63;
    wrapNotes(sheet, positions, 5, { D: 85, E: 67 });
  }
  if (name === "Operating inputs") {
    sheet.getRange("C1:C74").format.columnWidth = 40;
    sheet.getRange("D1:D74").format.columnWidth = 48;
    wrapNotes(sheet, positions, 66, { C: 44, D: 53 });
  }
  if (name === "Valuation inputs") {
    for (const [column, width] of Object.entries({ D: 24, E: 46, F: 70, G: 44, H: 55 })) {
      sheet.getRange(`${column}1:${column}${rows}`).format.columnWidth = width;
    }
    wrapNotes(sheet, positions, 5, { D: 27, E: 51, F: 78, G: 49, H: 60 });
  }
  if (name === "Comps") {
    sheet.freezePanes.freezeRows(4);
    sheet.getRange("M1:M16").format.columnWidth = 23;
    for (const row of [11, 20]) {
      const headers = sheet.getRange(`A${row}:O${row}`);
      headers.format.wrapText = true;
      headers.format.rowHeight = 25;
    }
    wrapNotes(sheet, positions, 30, { B: 18, D: 18, F: 18, H: 18 });
  }
  if (name === "DCF") {
    sheet.getRange("F115").format.fill = pale;
    sheet.getRange("F126").format.fill = pale;
  }
  if (name === "Checks") {
    sheet.getRange("C1:F14").format.columnWidth = 3;
    for (const area of ["G6:L11", "B13:B14"]) {
      sheet.getRange(area).conditionalFormats.addCustom(`ABS(${area.split(":")[0]})>0.01`,
        { fill: "#FCE8E6", font: { bold: true, color: "#A52620" } });
    }
  }
}

function wrapNotes(sheet, positions, firstRow, capacities) {
  const heights = new Map();
  for (const [address, row, , [value]] of positions) {
    const capacity = capacities[address.replace(/\d+/, "")];
    if (row + 1 < firstRow || !capacity || typeof value !== "string" || value.startsWith("=")) continue;
    sheet.getRange(address).format.wrapText = true;
    const height = 5 + Math.max(1, Math.ceil(value.length / capacity)) * 13;
    heights.set(row, Math.max(heights.get(row) ?? 15, height));
  }
  for (const [row, height] of heights) sheet.getRange(`A${row + 1}`).format.rowHeight = height;
}

function formatCover(sheet) {
  sheet.getRange("A1:B27").format.columnWidth = 4;
  sheet.getRange("C1:C27").format.columnWidth = 4;
  sheet.getRange("D1:D27").format.columnWidth = 36;
  sheet.getRange("E1:E27").format.columnWidth = 24;
  sheet.getRange("F1:G27").format.columnWidth = 5;
  sheet.getRange("H1:H27").format.columnWidth = 28;
  sheet.getRange("I1:I27").format.columnWidth = 17;
  sheet.getRange("C3:I4").format.fill = navy;
  for (const range of ["C5:I5", "C6:I6", "D26:I26", "D27:I27", "H11:I11", "H20:I20"]) {
    sheet.mergeCells(range);
  }
  sheet.getRange("C5:I5").format.font = { name: font, size: 22, bold: true, color: navy };
  sheet.getRange("C5:I5").format.rowHeight = 31;
  sheet.getRange("C5:I6").format.horizontalAlignment = "center";
  sheet.getRange("D11:I23").format.rowHeight = 20;
  sheet.getRange("D17:E22").format.fill = pale;
  sheet.getRange("D17:E23").format.borders = { preset: "outside", style: "thin", color: navy };
  sheet.getRange("E17:E19").format.font = { name: font, size: 13, bold: true, color: navy };
  sheet.getRange("E23").format.font = { name: font, size: 13, bold: true, color: "#FFFFFF" };
  const selected = sheet.getRange("E13");
  selected.format.fill = editable;
  selected.format.font = { name: font, size: 11, bold: true, color: inputBlue };
  selected.format.borders = { preset: "outside", style: "dashed", color: navy };
  selected.dataValidation = { rule: { type: "list", values: ["Bear", "Base", "Bull"] } };
}

const layouts = new Map();
for (const [name, sheet] of sheets) {
  const layout = populate(sheet, plan.cells[name]);
  layouts.set(name, layout);
  formatSheet(name, sheet, layout);
}

const selector = sheets.get("Cover").getRange("E13");
function verifyCase(name) {
  selector.values = [[name[0].toUpperCase() + name.slice(1)]];
  workbook.recalculate();
  const failures = [];
  let tested = 0;
  for (const [sheetName, layout] of layouts) {
    const grid = layout.used.values;
    for (const [address, row, column, [formula]] of layout.positions) {
      const actual = grid[row]?.[column];
      if (typeof formula === "string" && formula.startsWith("=") && typeof actual === "string" && errors.test(actual)) {
        failures.push(`${sheetName}!${address} ${actual}`);
      }
    }
    for (const [address, expected] of Object.entries(plan.expected[name][sheetName] ?? {})) {
      const [row, column] = indexes(address);
      const actual = grid[row]?.[column];
      const matches = typeof expected === "number"
        ? typeof actual === "number" && Math.abs(actual - expected) <= Math.max(1e-6, Math.abs(expected) * 1e-10)
        : actual === expected || (expected === null && (actual === "" || actual == null));
      if (!matches) failures.push(`${sheetName}!${address} returned ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);
      tested += 1;
    }
  }
  if (failures.length) throw new Error(`${name} case: ${failures.length} discrepancies\n${failures.slice(0, 20).join("\n")}`);
  console.log(`${name}: ${tested} checks against Python`);
}

for (const name of Object.keys(plan.expected).filter((name) => name !== plan.case)) verifyCase(name);
const activeDeliveryRow = { bear: 40, base: 41, bull: 42 }[plan.case];
selector.values = [[plan.case[0].toUpperCase() + plan.case.slice(1)]];
workbook.recalculate();
const lateDeliveries = sheets.get("Operating inputs").getRange(`F${activeDeliveryRow}`);
const revenue = sheets.get("Revenue model");
const targetCell = sheets.get("DCF").getRange("B46");
const initialDeliveries = lateDeliveries.values[0][0];
const initialHistoricalSales = revenue.getRange("F64").values[0][0];
const initialForecastSales = revenue.getRange("L64").values[0][0];
const initialTarget = targetCell.values[0][0];
lateDeliveries.values = [[initialDeliveries + 1]];
workbook.recalculate();
if (revenue.getRange("L13").values[0][0] !== initialDeliveries + 1
    || !(revenue.getRange("L64").values[0][0] > initialForecastSales)
    || !(targetCell.values[0][0] > initialTarget)
    || revenue.getRange("F64").values[0][0] !== initialHistoricalSales) {
  throw new Error("A late-year aircraft assumption failed to flow through revenue and valuation.");
}
lateDeliveries.values = [[initialDeliveries]];
verifyCase(plan.case);
const overview = await workbook.inspect({
  kind: "table", range: "Cover!D11:E23", include: "values,formulas", tableMaxRows: 13, tableMaxCols: 2, maxChars: 1900,
});
console.log(overview.ndjson);

if (previewDirectory) {
  await fs.mkdir(previewDirectory, { recursive: true });
  for (const [name, layout] of layouts) {
    const lastColumn = String.fromCharCode(64 + Math.min(layout.cols, name === "Cover" ? 9 : 16));
    const lastRow = Math.min(layout.rows, name === "Cover" ? 27 : 22);
    const image = await workbook.render({ sheetName: name, range: `A1:${lastColumn}${lastRow}`, scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDirectory, `${name.replaceAll(/[^a-z0-9]+/gi, "-")}.png`),
      new Uint8Array(await image.arrayBuffer()));
  }
}
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
