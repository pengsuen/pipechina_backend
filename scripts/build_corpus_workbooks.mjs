import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const jobs = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const output = process.argv[3];
const previews = path.join(output, '_qa_workbooks');
await fs.mkdir(previews, {recursive:true});
for (const job of jobs) {
  const wb = Workbook.create();
  const summary=wb.worksheets.add('复核汇总');
  const details=wb.worksheets.add('明细台账');
  const rules=wb.worksheets.add('规则与例外');
  details.getRange(`A1:G${job.rows.length}`).values=job.rows;
  details.tables.add(`A1:G${job.rows.length}`,true,'ReviewRecords');
  details.freezePanes.freezeRows(1);
  details.getRange(`A1:G${job.rows.length}`).format={font:{name:'Songti SC',size:11},wrapText:true,rowHeight:48,columnWidth:20};
  details.getRange(`C1:C${job.rows.length}`).format.columnWidth=38;
  details.getRange(`G1:G${job.rows.length}`).format.columnWidth=36;
  summary.mergeCells('A1:E2'); summary.getRange('A1').values=[[job.spec.title]];
  summary.getRange('A3:E3').values=[[job.spec.code,`版本 ${job.version}.0`,'归口单位',job.spec.station,job.spec.owner]];
  summary.getRange('A5:B10').values=[['复核结果','记录数'],['通过',null],['待补正',null],['待复核',null],['不适用',null],['合计',null]];
  summary.getRange('B6:B9').formulas=[6,7,8,9].map(r=>[`=COUNTIFS('明细台账'!F2:F${job.rows.length},A${r})`]);
  summary.getRange('B10').formulas=[['=SUM(B6:B9)']];
  summary.getRange('A12:B15').values=[['版本规则','要求'],['初审时限 工作日',job.version===1?3:2],['保存期限 年',job.version===1?5:8],['抽查比例',job.version===1?.10:.15]];
  summary.getRange('B15').setNumberFormat('0%');
  summary.getRange('A18:C24').values=[['月份','复核量','待补正量'],...job.trend];
  const chart=summary.charts.add('bar',summary.getRange('A18:C24'));
  chart.title='月度复核与补正'; chart.setPosition('D5','K21');
  const ruleRows=[['章节','条款与判断要求']];
  for(const sec of job.sections) for(const p of sec.paragraphs) ruleRows.push([sec.heading,p]);
  rules.getRange(`A1:B${ruleRows.length}`).values=ruleRows;
  rules.getRange(`A1:B${ruleRows.length}`).format={font:{name:'Songti SC',size:11},wrapText:true,rowHeight:100};
  rules.getRange(`A1:A${ruleRows.length}`).format.columnWidth=30;
  rules.getRange(`B1:B${ruleRows.length}`).format.columnWidth=110;
  rules.freezePanes.freezeRows(1);
  summary.getRange('A1:K26').format.font={name:'Songti SC',size:11};
  summary.getRange('A1:K26').format.columnWidth=18;
  summary.getRange('A1:K26').format.rowHeight=25;
  summary.getRange('A1:E2').format.font={name:'Songti SC',size:17,bold:true};
  for(const sheet of [summary,details,rules]){
    sheet.showGridLines=false;
    sheet.getRange(sheet===summary?'A5:B5':sheet===details?'A1:G1':'A1:B1').format={fill:'#234d66',font:{name:'Songti SC',color:'#FFFFFF',bold:true}};
  }
  wb.recalculate();
  const total=summary.getRange('B10').values[0][0];
  if(total!==job.rows.length-1) throw new Error(`Bad count ${total}`);
  await (await SpreadsheetFile.exportXlsx(wb)).save(job.path);
  const preview=await wb.render({sheetName:'复核汇总',range:'A1:K26',scale:1,format:'png'});
  await fs.writeFile(path.join(previews,path.basename(job.path,'.xlsx')+'.png'),new Uint8Array(await preview.arrayBuffer()));
  console.log(`workbook ${job.path} rows=${total}`);
}
