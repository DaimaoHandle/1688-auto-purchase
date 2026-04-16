var results = [];
var all = document.querySelectorAll('textarea, input, [contenteditable], [role="textbox"], [class*="editor"], [class*="input"], [class*="chat"], [class*="send"], iframe');
for (var i = 0; i < all.length; i++) {
    var el = all[i];
    var r = el.getBoundingClientRect();
    if (r.width < 5 && r.height < 5) continue;
    results.push(el.tagName + ' | cls=' + String(el.className).slice(0,50) + ' | id=' + (el.id||'') + ' | contenteditable=' + el.getAttribute('contenteditable') + ' | role=' + el.getAttribute('role') + ' | ' + Math.round(r.width) + 'x' + Math.round(r.height) + ' | txt=' + String(el.innerText||el.value||'').slice(0,20));
}
console.log('找到 ' + results.length + ' 个候选元素:');
for (var j = 0; j < results.length; j++) console.log(results[j]);
// 也检查 iframe
var iframes = document.querySelectorAll('iframe');
console.log('iframe 数量: ' + iframes.length);
for (var k = 0; k < iframes.length; k++) {
    console.log('iframe[' + k + '] src=' + (iframes[k].src||'').slice(0,80) + ' ' + Math.round(iframes[k].getBoundingClientRect().width) + 'x' + Math.round(iframes[k].getBoundingClientRect().height));
}
