(function () {
  var theme = 'dark'
  try {
    theme = localStorage.getItem('ciclotrade.theme') === 'light' ? 'light' : 'dark'
  } catch {}
  var root = document.documentElement
  root.dataset.theme = theme
  var themeColor = document.querySelector('meta[name="theme-color"]')
  if (themeColor) themeColor.setAttribute('content', theme === 'light' ? '#dbe2db' : '#0b0d0c')
})()
