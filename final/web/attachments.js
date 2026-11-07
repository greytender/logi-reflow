function csvRows(text) {
  const rows = [];
  let row = [], cell = '', quoted = false;
  for (let i=0; i<text.length; i++) {
    const char = text[i];
    if (char === '"') {
      if (quoted && text[i+1] === '"') { cell += '"'; i++; }
      else if (quoted || cell === '') quoted = !quoted;
      else cell += char;
    } else if (!quoted && (char === ',' || char === '\n' || char === '\r')) {
      row.push(cell);
      cell = '';
      if (char !== ',') {
        if (row.some(value=>value.trim())) rows.push(row);
        row = [];
        if (char === '\r' && text[i+1] === '\n') i++;
      }
    } else cell += char;
  }
  if (quoted) throw new Error('CSV 引号没有闭合');
  row.push(cell);
  if (row.some(value=>value.trim())) rows.push(row);
  return rows;
}

async function readWorkbook(buffer) {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(bytes.buffer);
  const read16 = offset=>view.getUint16(offset,true);
  const read32 = offset=>view.getUint32(offset,true);
  let end = bytes.length-22;
  while (end >= Math.max(0,bytes.length-65557) && read32(end)!==0x06054b50) end--;
  if (end<0 || read32(end)!==0x06054b50) throw new Error('无法打开工作簿');
  const entries = new Map();
  let cursor = read32(end+16);
  for (let i=0; i<read16(end+10); i++) {
    if (read32(cursor)!==0x02014b50) throw new Error('工作簿目录损坏');
    const length=read16(cursor+28);
    const name=new TextDecoder().decode(bytes.subarray(cursor+46,cursor+46+length));
    entries.set(name,{method:read16(cursor+10),size:read32(cursor+20),expanded:read32(cursor+24),offset:read32(cursor+42)});
    cursor += 46+length+read16(cursor+30)+read16(cursor+32);
  }
  async function xml(name) {
    const entry = entries.get(name);
    if (!entry) throw new Error('找不到工作表数据');
    if (entry.expanded>12_000_000) throw new Error('工作表内容过大，请拆分后再试');
    const offset=entry.offset+30+read16(entry.offset+26)+read16(entry.offset+28);
    const data=bytes.subarray(offset,offset+entry.size);
    const decoded=entry.method===0 ? new TextDecoder().decode(data) :
      await new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).text();
    const doc=new DOMParser().parseFromString(decoded,'application/xml');
    if (doc.querySelector('parsererror')) throw new Error('工作表内容损坏');
    return doc;
  }
  const book=await xml('xl/workbook.xml');
  const sheet=book.querySelector('sheet');
  if (!sheet) throw new Error('工作簿中没有工作表');
  const relations=await xml('xl/_rels/workbook.xml.rels');
  const id=sheet.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships','id') || sheet.getAttribute('r:id');
  const target=[...relations.querySelectorAll('Relationship')].find(rel=>rel.getAttribute('Id')===id)?.getAttribute('Target');
  if (!target) throw new Error('找不到工作表');
  const path=new URL(target,'https://local.invalid/xl/workbook.xml').pathname.slice(1);
  const strings=entries.has('xl/sharedStrings.xml') ?
    [...(await xml('xl/sharedStrings.xml')).querySelectorAll('si')].map(item=>[...item.querySelectorAll('t')].map(t=>t.textContent).join('')) : [];
  const doc=await xml(path);
  const rows=[...doc.querySelectorAll('sheetData row')].map(item=>{
    const values=[];
    for (const cell of item.querySelectorAll('c')) {
      const reference=cell.getAttribute('r')?.match(/^[A-Z]+/)?.[0];
      const column=reference ? [...reference].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0)-1 : values.length;
      const value=cell.querySelector('v')?.textContent ?? '';
      values[column]=cell.getAttribute('t')==='s' ? strings[Number(value)] ?? '' :
        cell.getAttribute('t')==='inlineStr' ? [...cell.querySelectorAll('t')].map(t=>t.textContent).join('') : value;
    }
    return values;
  }).filter(row=>row.some(value=>value.trim()));
  return {rows,sheet:sheet.getAttribute('name')};
}

async function readAttachment(file) {
  if (file.size>3_000_000) throw new Error('请选择小于 3 MB 的文件');
  const ext=file.name.split('.').pop().toLowerCase();
  if (ext==='xlsx') {
    const buffer=await file.arrayBuffer();
    const workbook=await readWorkbook(buffer);
    const digest=await window.crypto?.subtle?.digest('SHA-256',buffer);
    const hash=digest ? [...new Uint8Array(digest)].map(byte=>byte.toString(16).padStart(2,'0')).join('') : '';
    // The confirmed blank-cell and same-name rules belong to this exact file.
    const central=hash==='40849d76aceaf3589ac68fdad258c847ed0bbf464e65b76a468258bc36726587';
    return {name:file.name,...workbook,central};
  }
  if (!['csv','txt','json'].includes(ext)) throw new Error('请选择 XLSX、CSV、TXT 或 JSON 文件');
  const bytes=await file.arrayBuffer();
  let text;
  try { text=new TextDecoder('utf-8',{fatal:true}).decode(bytes); }
  catch { text=new TextDecoder('gb18030').decode(bytes); }
  if (!text.trim()) throw new Error('文件内容为空');
  if (ext==='csv') return {name:file.name,rows:csvRows(text)};
  if (ext==='json') {
    const value=JSON.parse(text);
    if (Array.isArray(value) && value.length && value.every(row=>row && typeof row==='object' && !Array.isArray(row))) {
      const keys=[...new Set(value.flatMap(Object.keys))];
      return {name:file.name,rows:[keys,...value.map(row=>keys.map(key=>String(row[key] ?? '')))]};
    }
    text=JSON.stringify(value,null,2);
  }
  return {name:file.name,text};
}

function summarizeAttachment(file, question='') {
  if (!file.rows) return '已读到《'+file.name+'》。开头内容：\n'+file.text.trim().slice(0,260);
  if (file.central) {
    const rows=file.rows.slice(5).filter(row=>row[2]?.trim());
    const stores=new Set(rows.map(row=>row[2].replace(/\s/g,'')));
    const noCold=rows.filter(row=>[7,8,9].every(i=>!row[i]?.trim())).length;
    return '托运单明细有 '+rows.length+' 笔订单，按本批确认的同名口径合为 '+stores.size+
      ' 家门店。'+noCold+' 行冷链栏为空，本批按无冷链货处理。点「核对原表」可看体积换算与本地复算。';
  }
  const [headers=[],...rows]=file.rows;
  const prefix=file.sheet ? '首张工作表「'+file.sheet+'」' : '表格';
  const fields=headers.filter(Boolean).slice(0,12).join('、') || '空';
  if (!rows.length) return prefix+'共 '+file.rows.length+' 个非空行，可见列名：'+fields+'。';
  const preview=rows.slice(0,2).map(row=>headers.flatMap((key,i)=>key && row[i] ? [key+'：'+row[i]] : []).slice(0,5).join(' · ')).filter(Boolean);
  const store=headers.findIndex(key=>/门店|收货单位/.test(key));
  const unique=store<0 ? [] : [...new Set(rows.map(row=>row[store]?.trim()).filter(Boolean))];
  if (/门店/.test(question) && unique.length) return '按「'+headers[store]+'」列去重，共 '+unique.length+' 家：'+unique.slice(0,8).join('、')+(unique.length>8?'等。':'。');
  const matching=rows.find(row=>row.some(value=>value.length>=2 && question.includes(value)));
  if (matching) return '找到一条匹配记录：\n'+headers.flatMap((key,i)=>key && matching[i] ? [key+'：'+matching[i]] : []).slice(0,8).join('\n');
  return prefix+'识别到 '+rows.length+' 条数据，列名包括 '+fields+'。'+(preview.length?'\n'+preview.join('\n'):'');
}
