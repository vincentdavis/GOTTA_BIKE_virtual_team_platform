# ZwiftRacing.app Public APIs

## Clubs

#### /public/clubs/\<id\>

| Method | GET |
| :---- | :---- |
| Example URL | https://api.zwiftracing.app/api/public/clubs/11818 |
| Description | Returns data for active members of the \<id\> club sorted by riderId  Limited to 1000 results. |
| Rate Limits | Standard \- 1 call every 60 minutes Premium \- 10 calls every 60 minutes |

#### /public/clubs/\<id\>/\<riderId\>

| Method | GET |
| :---- | :---- |
| Example URL | https://api.zwiftracing.app/api/public/clubs/11818/100000 |
| Description | Returns data for active members of the \<id\> club sorted by riderId with riderId greater than \<riderId\> Limited to 1000 results. |
| Rate Limits | Standard \- 1 call every 60 minutes Premium \- 10 calls every 60 minutes |

## Results

#### /public/results/\<eventId\>

| Method | GET |
| :---- | :---- |
| Example URL | https://api.zwiftracing.app/api/public/results/4879983 |
| Description | Returns ZwiftRacing.app results for \<eventId\> |
| Rate Limits | Standard \- 1 call every 1 minute Premium \- 1 call every 1 minute |

#### /public/zp/\<eventId\>/results

| Method | GET |
| :---- | :---- |
| Example URL | https://api.zwiftracing.app/api/public/zp/4879983/results |
| Description | Returns ZwiftPower results for \<eventId\> |
| Rate Limits | Standard \- 1 call every 1 minute Premium \- 1 call every 1 minute |

## Riders

#### /public/riders/\<riderId\>

| Method | GET |
| :---- | :---- |
| Example URL | https://api.zwiftracing.app/api/public/riders/5574 |
| Description | Returns current Rider data for \<riderId\> |
| Rate Limits | Standard \- 5 calls every 1 minute Premium \- 10 calls every 1 minute |

#### /public/riders/\<riderId\>/\<time\>

| Method | GET |
| :---- | :---- |
| Example URL | https://api.zwiftracing.app/api/public/riders/5574/1735689600 |
| Description | Returns current Rider data for \<riderId\> at a given \<time\>. Time needs to be an epoch without milliseconds. |
| Rate Limits | Standard \- 5 calls every 1 minute Premium \- 10 calls every 1 minute |

#### /public/riders

| Method | POST |
| :---- | :---- |
| Example URL | https://api.zwiftracing.app/api/public/riders |
| Example Body | \[8, 5574\] |
| Description | Returns current Rider data for each riderId in the array (limit 1000). |
| Rate Limits | Standard \- 1 call every 15 minutes Premium \- 10 call every 15 minutes |

#### /public/riders/\<time\>

| Method | POST |
| :---- | :---- |
| Example URL | https://api.zwiftracing.app/api/public/riders/1735689600 |
| Example Body | \[8, 5574\] |
| Description | Returns current Rider data for each riderId in the array at a given \<time\>. Time needs to be an epoch without milliseconds. |
| Rate Limits | Standard \- 1 call every 15 minutes Premium \- 10 call every 15 minutes |

#### 