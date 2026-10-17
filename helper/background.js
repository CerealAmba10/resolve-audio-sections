// Answers the post card on each site: which post is next, its clip, and status changes.
browser.runtime.onMessage.addListener(async (message, sender) => {
  if (message.type === 'next') {
    const platform = sender.tab && platformFor(sender.tab.url);
    return platform ? nextPost(await getPosts(), platform) : null;
  }
  if (message.type === 'clip') {
    const blob = await files('readonly', store => store.get(message.id));
    // Sent as bytes: plain data crosses into the page's content script reliably.
    return blob ? {name: message.name, type: blob.type || 'video/mp4', bytes: await blob.arrayBuffer()} : null;
  }
  if (message.type === 'status') {
    const posts = await getPosts();
    const post = posts.find(p => p.id === message.id);
    if (post) post.status = message.status;
    await setPosts(posts);
    return true;
  }
});

browser.browserAction.onClicked.addListener(() => browser.tabs.create({url: 'helper.html'}));
browser.runtime.onInstalled.addListener(() => browser.tabs.create({url: 'helper.html'}));
