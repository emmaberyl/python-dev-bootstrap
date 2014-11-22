from django.shortcuts import render
from github.models import *
from django.template import RequestContext, loader
from django.http import HttpResponse
import requests, json, datetime, pickle, sys
from django.db.models.base import ObjectDoesNotExist
import logging

log = logging.getLogger(__name__)
apiBaseURL = 'https://api.github.com/'

# Create your views here.
def QueueStatus(request):
    myRateLimit = RateLimit.objects.all()
    template = loader.get_template('github/ratelimit.html')
    context = RequestContext(request, { 'myRateLimit': myRateLimit})
    return HttpResponse(template.render(context))
    
def populate(request):
    #get open source repositories
    url = 'repositories'
    r = MakeGitHubRequest(url)
    output = ""
    counter = 0
    if r:
        for remoteRepo in r:
            # try to look up this repo
            counter+=1
            if counter == 2:
                break
            output += "\n-------------\n Json Object"
            output += json.dumps(remoteRepo, indent=4, separators=(',', ': ')) 
            output += "\nGetting id:" + str(remoteRepo["id"])
            
            try:
                myRepo = Repo.objects.get(pk = remoteRepo["id"])
            except ObjectDoesNotExist:
                myRepo = Repo(id = remoteRepo["id"])
            finally:
                # update base description
                myRepo.full_name = remoteRepo["full_name"]
                myRepo.description = remoteRepo["description"]
                myRepo.html_url = remoteRepo["html_url"]
                myRepo.save()
                
                # get pull requests
                # get request is dependant on ratelimiting parameters that we may have captured
                remotePullRequestURL = "repos/" + myRepo.full_name + "/pulls?state=all"
                log.info("pulls url: " + remotePullRequestURL)
                remotePullRequests = MakeGitHubRequest(remotePullRequestURL)
                
                log.info("importing pull requests")
                for remotePullRequest in remotePullRequests:
                    try:
                        myPullRequest = myRepo.pullrequest_set.get(pk=remotePullRequest['number'])
                    except ObjectDoesNotExist:
                        myPullRequest = PullRequest(number = remotePullRequest['number'], repo = myRepo)
                    
                    #figure out if we need to create the user
                    try:
                        myUser = User.objects.get(pk = remotePullRequest['user']['id'])
                    except:
                        myUser = User(pk = remotePullRequest['user']['id'], login = remotePullRequest['user']['login'])
                        myUser.save()
                        
                    myPullRequest.user = myUser
                    
                    myPullRequest.state = remotePullRequest['state']
                    myPullRequest.created_at = remotePullRequest['created_at']
                    myPullRequest.updated_at = remotePullRequest['updated_at']
                    myPullRequest.closed_at = remotePullRequest['closed_at']
                    myPullRequest.merged_at = remotePullRequest['merged_at']

                    myPullRequest.save()
                    
                output += str(list(myRepo.pullrequest_set.all()))
                
        #break
        # update stats on repo
    return HttpResponse("<pre>" + output + "</pre>")

def RefreshRateLimitStats():
    rateInfo = MakeGitHubRequest('rate_limit')
    try:
        myRequestCache = GitHubRequestCache.objects.get(pk = url)
        log.debug("Using ETag: {0}".format(myRequestCache.ETag))
    except ObjectDoesNotExist:
        log.debug("Unknown API Request")
        myRequestCache = GitHubRequestCache(pk = url, ETag = '')
    
def MakeGitHubRequest(url):
    log.info("Making GitHubRequest: {0}".format(url))
    #look up our query to see if we can make a conditional request
    try:
        log.debug("Checking cache")
        myRequestCache = GitHubRequestCache.objects.get(pk = url)
        log.debug("Using ETag: {0}".format(myRequestCache.ETag))
    except ObjectDoesNotExist:
        log.debug("Unknown API Request")
        myRequestCache = GitHubRequestCache(pk = url, ETag = '')
    
    # Make the Request
    headers = {'If-None-Match':myRequestCache.ETag}
    r = requests.get(apiBaseURL + url, headers=headers)
    
    #record the new ETag
    myRequestCache.ETag = r.headers['ETag']
    
    log.debug("Saving ETag: {0}".format(r.headers['ETag']))
    myRequestCache.save()
    
    #record rate limiting for status page
    type="core"
    if url.startswith("search"):
        type="search"
    
    try:
        myRateLimit = RateLimit.objects.get(pk = type)
    except ObjectDoesNotExist:
        myRateLimit = RateLimit(type = type)
    
    myRateLimit.limit = r.headers['X-RateLimit-Limit']
    myRateLimit.remaining = r.headers['X-RateLimit-Remaining']
    s = int(r.headers['X-RateLimit-Reset'])
    myRateLimit.reset = datetime.datetime.fromtimestamp(s).strftime('%Y-%m-%d %H:%M:%S.%f')
    myRateLimit.save()
    
    # check return status code
    if r.status_code == 200:
        log.debug("found new data")
        return r.json()
    elif r.status_code == 403:
        log.exception("Rate limit has been exceeded")
        raise Exception("Rate limit has been exceeded")
    elif r.status_code == 304:
        #resource has not been modified, no processing needed
        log.debug("No new data found")
        return None
    else:
        raise Exception("Unknown StatusCode: {0}, {1}".format(r.status_code, url))