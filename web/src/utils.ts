export function downloadFile(href: string, filename: string = ''): void {
  const a = document.createElement('a');
  a.target = '_blank';
  a.href = href;
  a.download = filename;

  document.body.appendChild(a);
  a.dispatchEvent(new MouseEvent('click'));
  document.body.removeChild(a);
}
