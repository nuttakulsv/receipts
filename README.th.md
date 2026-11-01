# receipts

[English](README.md) · จากคนทำ [honest-tests](https://github.com/nuttakulsv/honest-tests) และ [leave-no-trace](https://github.com/nuttakulsv/leave-no-trace)

"✅ All tests pass." ผ่านจริงหรือเปล่า? agent รันเทสต์หลังแก้ครั้งสุดท้ายแล้วหรือยัง? มันอ่าน output จริงไหม หรือแค่เห็นว่า `| tail` จบด้วย exit 0?

receipts เป็น hook ของ Claude Code ที่เอาข้อความสรุปตอนจบของ agent มาเทียบกับสิ่งที่เกิดขึ้นจริงใน session ถ้าสรุปอ้างอะไรที่ไม่มีหลักฐานใน session agent จะถูกส่งกลับไปแก้คำตอบก่อนที่เราจะได้อ่าน

## ตรวจอะไรบ้าง

| agent บอกว่า | receipts หาหลักฐานใน session ว่า |
|---|---|
| "All tests pass" | มีคำสั่งรันเทสต์**หลังการแก้ไขครั้งสุดท้าย**และไม่ fail |
| "build ผ่าน", "lint สะอาด", "ไม่มี type error" | เหมือนกัน แต่ดูคำสั่ง build, lint และ type check |
| "ผ่านทั้ง 47 เทสต์" | มีรอบที่รายงานว่าผ่าน 47 จริง |
| "รัน \`npm run e2e\` แล้ว" | มีคำสั่งนั้น หรือคำสั่งที่มีคำสั่งนั้นอยู่ข้างใน |
| "แก้ \`src/api.ts\` แล้ว" | มีการแก้ไฟล์นั้น หรือมีคำสั่ง shell ที่แตะไฟล์นั้น |
| "ลบ \`old.py\` แล้ว" | ไฟล์หายไปจริง |
| "เทสต์ที่ fail พังอยู่ก่อนแล้ว / flaky / ไม่เกี่ยวกับที่แก้" | มีการรันโดยไม่มีโค้ดที่ agent แก้ (`git stash`, worktree, [was-it-me](https://github.com/nuttakulsv/was-it-me)) หรือเทสต์ตัวเดียวกันทั้งผ่านและ fail โดยไม่มีการแก้ระหว่างนั้น |

ต่อให้ exit code เป็น 0 ถ้า output บอกว่าพัง ก็นับว่าพัง เช่น `2 failed`, `FAIL`, `error TS2345` หรือมี traceback ของ Python ดังนั้น `npm test | tail -20` ก็ไม่รอด

ตอนจับได้ agent จะเห็นข้อความแบบนี้

```
receipts: parts of your final message don't match what happened in this session:

- You said "All tests pass now.", but you changed /repo/src/cart.ts after the last tests run (`npm test`).
- You said you updated `docs/api.md`, but nothing in this session wrote to it.

Fix your reply before you finish: ...
```

ส่วนใหญ่มันจะไปรันเทสต์ใหม่แล้วเขียนสรุปตามจริง บางครั้งเทสต์ก็ fail ซึ่งก็คือสิ่งที่เราอยากรู้ตั้งแต่แรก

## อะไรที่ไม่โดน

- ประโยคที่ยังไม่ยืนยันหรือเป็นแผน เช่น "น่าจะผ่านหลังแก้ fixture", "ยังไม่ได้รันเทสต์", "อย่าลืมเช็คว่า build ผ่าน"
- การอ้างถึงสิ่งที่ไม่ใช่คำสั่งหรือไฟล์
- การหยุดซ้ำสองครั้ง ถ้า agent ถูกส่งกลับไปแล้วรอบหนึ่ง รอบต่อไปจะผ่านเลย ไม่มีทางติดลูป

## ติดตั้ง

Claude Code

```
/plugin marketplace add nuttakulsv/receipts
/plugin install receipts@receipts
```

ส่วน skill ใช้กับ Codex, Cursor หรือ agent อื่นที่อ่าน SKILL.md ได้ เป็นกฎสั้นๆ สำหรับเขียนสรุปตอนจบให้รายงานแค่สิ่งที่เห็นจริง

```
npx skills add nuttakulsv/receipts
```

## ทำงานยังไง

ตอน agent จะหยุด hook จะอ่าน transcript ที่ Claude Code เก็บไว้อยู่แล้ว (ทุกคำสั่ง output ว่า fail หรือไม่ และการแก้ไฟล์ทุกครั้ง) กับข้อความสุดท้าย แล้วใช้ regular expression หาข้อความที่เป็นการอ้างผลลัพธ์ เอาไปเทียบกับ transcript ทีละข้อ ใช้แค่ standard library ของ Python ไม่ต่อเน็ต ไม่มีอะไรออกจากเครื่อง

## ข้อจำกัด

- หาข้อความที่อ้างผลลัพธ์จาก pattern ภาษาอังกฤษ ถ้าเขียนแปลกๆ ก็หลุดได้
- ถ้า agent แก้ไฟล์ผ่านสคริปต์ที่มันรัน (เช่น Python บรรทัดเดียวที่เขียนทับไฟล์) จะไม่นับเป็นการแก้ แต่ถ้าแก้ผ่าน Edit/Write, `sed -i`, `git apply` หรือ redirect `> file.py` จะนับ
- จำคำสั่งรันเทสต์จากชื่อ เช่น pytest, jest, vitest, go test, cargo test, rspec, `npm test`, `make test` และอีกราวสามสิบตัว สคริปต์ของเราเองอย่าง `./ci.sh` จะไม่รู้จัก เปิด issue บอกมาได้
- ตรวจแค่สิ่งที่อ้าง ไม่ได้ตรวจคุณภาพของงาน บอกได้ว่าเทสต์รันหลังการแก้ครั้งสุดท้าย แต่บอกไม่ได้ว่าเทสต์ตรวจถูกเรื่อง ถ้าอยากตรวจตรงนั้นดู [honest-tests](https://github.com/nuttakulsv/honest-tests)

## รันเทสต์

```
python3 hooks/receipts.py --test
```

## License

MIT
